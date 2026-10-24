# Modelo de datos

La base de Minerva es **un archivo SQLite** (modo WAL) con **28 tablas**, 13 índices explícitos y un
disparador. Este documento explica para qué sirve cada tabla, quién la escribe y quién la lee, qué
la protege de cambios, cómo evoluciona el esquema sin romper una base que ya tiene datos y cómo
agregar una columna o una tabla. El esquema tabla por tabla (columnas, tipos, `CHECK`, índices) está
en [`../ESQUEMA_BD.md`](../ESQUEMA_BD.md), que **se genera** desde el código y una prueba falla si
no coincide: no lo copies aquí.

Dónde vive el código: `app/persistence/database.py` (el esquema `SCHEMA`, las migraciones y la clase
`Database`), `app/persistence/models.py` (los modelos pydantic), `app/persistence/repositories/`
(un módulo por familia de tablas). La guía por archivo está en
[`modulos/persistence.md`](modulos/persistence.md).

## Convenciones

- **Una sola conexión** `aiosqlite` compartida por todo el proceso, con un candado de escritura
  (`Database._write_lock`). `execute` y `execute_many` toman el candado y hacen `commit`;
  `fetch_one` y `fetch_all` leen sobre la misma conexión sin candado. `connect()` activa
  `journal_mode=WAL` y `synchronous=NORMAL` y ejecuta el esquema.
- **Las fechas** son texto ISO-8601 en UTC; los **tiempos de velas y de funding**, enteros en
  milisegundos Unix (`open_time`, `funding_time`, `*_ms`).
- **Se accede por nombre de columna** (`row["symbol"]`, con `aiosqlite.Row`), nunca por posición, y
  las lecturas son `SELECT *` mapeadas a un modelo pydantic con una función `row_to_*`. Es lo que
  permite que una base migrada (con las columnas nuevas al final) y una base nueva se comporten igual.
- **Sin claves foráneas.** No hay `FOREIGN KEY` ni `PRAGMA foreign_keys`: las referencias
  (`signal_id`, `shadow_trade_id`, `trade_id`) y la unión entre tablas por `signal_group_key` son
  convención del código. Borrar una fila no arrastra a las demás.
- **Idempotencia por restricción, no por código.** Las claves `UNIQUE` más `INSERT OR IGNORE` son
  la defensa contra duplicados (una vela, un grupo de señales, una noticia no se procesan dos
  veces). Quien inserta debe tratar un `None` / `rowcount == 0` como "ya existía", no como error.
- **`signal_group_key`** = `símbolo|dirección|cierre de la vela` (`shadow_book.signal_group_key`)
  es el hilo que une la sombra (`shadow_trades`), su etiqueta (`llm_logs`), su memoria
  (`memory_entries`) y la decisión del brazo (`paper_execution_log`).
- **Modo de la columna `id`:** `INTEGER PRIMARY KEY AUTOINCREMENT` en las tablas de eventos; claves
  naturales compuestas en las cachés de mercado.

## Mapa de tablas

Los grupos son los de [`ESQUEMA_BD.md`](../ESQUEMA_BD.md#indice-por-tema). "Escribe" y "Lee" son los
componentes de negocio; todos acceden a través del repositorio de la tabla (`*_repo.py`). La lista
de archivos exacta de cada tabla sale de los hechos derivados del código de las
[guías por paquete](modulos/persistence.md).

### Cuenta paper, riesgo y control

| Tabla | Para qué sirve | Escribe | Lee |
|---|---|---|---|
| `trades` | las operaciones de la cuenta paper (abiertas y cerradas) con sus niveles de riesgo | `PaperBackend` (abrir y cerrar), `PositionMonitor` (trailing, funding, reconciliación), `funding_accrual` | motor de riesgo, monitor, scheduler, `market_features` (posiciones abiertas al decidir), rutas `GET /trades`, `GET /positions`, `GET /api/account` y `GET /api/risk`, Telegram, push, reporte de dinero real, post-mortem |
| `system_state` | estado clave-valor del sistema (`key`, `value`, `updated_at`): pausa, kill switch, riesgo, marcas de tiempo | motor de riesgo, `control_state`, monitor, brazo ejecutado, generador de señales, refresco del universo, push, comandos de Telegram | casi todo el núcleo; `GET /health`; scripts de estado |
| `risk_rejections` | cada entrada que el motor de riesgo rechazó, con el motivo y los valores exactos | `risk_engine` | `GET /api/risk` |
| `paper_execution_log` | una fila por cada decisión `APROBADA` que el brazo ejecutado consideró, abra o no (`OPENED` / `SKIPPED` con motivo) | `PaperExecutionArm` | `GET /api/arm`, reporte de dinero real, `scripts/paper_execution_status.py` |
| `control_audit` | rastro auditable de cada pausa, reanudación y kill switch (canal, actor, estado antes y después) | `control_actions` | `GET /api/control/audit`, push (origen de un cambio de riesgo) |

Claves de `system_state` que el código define (todas texto): `scheduler_paused` (`control_state`),
`kill_switch_active`, `drawdown_stop_active`, `drawdown_breach_in_progress`,
`drawdown_stop_would_have_triggered_count`, `equity_peak_usdt`, `consecutive_losses`,
`circuit_breaker_until`, `daily_loss_day`, `daily_loss_baseline_usdt`, `daily_loss_limit_locked` y
`drawdown_stop_last_resumed_at` (`risk_engine`), `monitor_last_seen_ms` (`PositionMonitor`),
`signals_stale_data_count` (generador de señales), `universe_last_refresh_error` (universo),
`push_state` (push) y `real_arm_started_at` / `real_arm_drawdown_baseline` (brazo ejecutado,
**escritura única**). Una clave nueva no necesita migración: es una fila más.

### Señales, sombras y medición del LLM

| Tabla | Para qué sirve | Escribe | Lee |
|---|---|---|---|
| `signals` | una fila por evaluación de (símbolo, estrategia, timeframe, vela cerrada), HOLD incluido; es el registro auditable completo | `signal_generator` (vía `signals_repo`, `INSERT OR IGNORE`; solo `status` se actualiza después) | `build_features_from_shadow_trade` (indicadores al decidir), memoria, `measurement_status` (marcas de tiempo del uptime), reporte de dinero real |
| `signals_discarded_by_sl_cap` | señales accionables descartadas por superar `LIVE_SL_MARGIN_CAP_PCT`, antes de cualquier sombra | `signal_generator` | `signals_repo` |
| `shadow_trades` | las operaciones sombra, una por señal agrupada, con margen ilimitado, y su etiqueta `llm_decision` | `ShadowBook` (abre), `PositionMonitor` (cierra, trailing, funding, reconciliación), `LlmDecisionService` (la etiqueta, una vez), `Scheduler` (`mark_executed`) | el etiquetador, la API (`GET /api/shadow-trades/recent`, `GET /api/llm/decisions/recent`), `measurement_status`, informes del veredicto y del placebo, memoria |
| `llm_logs` | una fila por llamada al LLM, exitosa o no, con el prompt completo, su hash y el costo | `LlmDecisionService` (solo `INSERT`; nunca se actualiza) | `GET /health`, `GET /api/llm/decisions/recent`, presupuesto diario, `measurement_status`, informes, `scripts/measurement_identity.py` |

### Mercado, universo y salud de las fuentes

| Tabla | Para qué sirve | Escribe | Lee |
|---|---|---|---|
| `ohlcv_cache` | velas cacheadas, clave (`symbol`, `interval`, `price_type`, `open_time`) | `ohlcv_history.download_missing` (generador, monitor, `download_history.py`, `repair_ohlcv_gaps.py`) | generador, `market_features`, reconciliación del monitor, backtest, reportes |
| `ohlcv_floor` | recuerda que ya se alcanzó el comienzo real del historial de Bitunix para una serie (también guarda el piso del funding con el intervalo `__funding__`) | `ohlcv_history`, `funding_history` | las mismas descargas |
| `ohlcv_series_complete` | marca afirmativa de "esta serie se descargó completa" | solo `scripts/download_history.py` | `ohlcv_history.get_cached_or_raise` |
| `ohlcv_unrepairable_gaps` | huecos de velas que ya se intentó reparar y no aparecen, con un enfriamiento de reintento | `ohlcv_history` | `ohlcv_history` |
| `funding_cache` | historial de funding, clave (`symbol`, `funding_time`) | `funding_history.download_missing_funding` | generador, `funding_accrual`, `market_features`, backtest |
| `contract_specs_cache` | especificación del contrato (tamaño mínimo, precisión, tramos de margen, intervalo de funding) | `contract_specs.refresh_spec` | `PaperBackend`, brazo ejecutado (tamaño mínimo), `ShadowBook`, liquidación, generador, backtest |
| `asset_universe` | snapshot del universo en cada refresco (todos los candidatos evaluados, con su motivo de exclusión) | `UniverseRefresher` (vía `universe_repo`) | generador, scheduler (suscripciones), colector de noticias, API, push, reporte de dinero real |
| `data_source_health` | salud por fuente: último éxito, último error, fallos seguidos (`ws_feed`, `news:<id>`) | `PositionMonitor` (latido), `NewsCollector` | motor de riesgo (bloquea aperturas con el feed obsoleto), `/health`, `news_status.py` |

### Backtest (Fase 2)

| Tabla | Para qué sirve | Escribe | Lee |
|---|---|---|---|
| `backtest_runs` | resultados históricos por (estrategia, símbolo, timeframe, segmento) | `backtesting/report.py` (vía `backtest_repo`) | informes |
| `backtest_trades` | las operaciones del backtest | `backtesting/report.py` | `analyze_risk.py`, `compare_fase2_runs.py` |
| `backtest_skipped_entries` | entradas omitidas (tope de riesgo, solape) | `backtesting/report.py` | `compare_fase2_runs.py` |
| `backtest_verdicts` | veredicto de descarte por estrategia | `backtesting/report.py` | `GET /api/backtest/verdicts`, `compare_fase2_runs.py` |

No son la medición en vivo del LLM. Las escribe `scripts/run_backtest.py`, no el bot.

### Noticias (Fase 4)

| Tabla | Para qué sirve | Escribe | Lee |
|---|---|---|---|
| `news_items` | titulares recolectados (solo título, URL, dominio y fecha; texto **no confiable**) | `NewsCollector` | `GET /api/news/recent`, contexto de noticias (prompt v5, sin uso), memoria |
| `news_fetch_state` | estado de la consulta condicional por fuente (ETag, `Last-Modified`, próxima consulta permitida) | `NewsCollector` | `NewsCollector`, `news_status.py` |
| `fear_greed` | índice Fear & Greed diario (la atribución a alternative.me es obligatoria) | `NewsCollector` | `GET /api/news/status`, contexto, memoria |

### Memoria de lecciones (Fase 4)

| Tabla | Para qué sirve | Escribe | Lee |
|---|---|---|---|
| `memory_entries` | una entrada **inmutable** por operación cerrada y fiable, con lo que se sabía al decidir y el resultado | `refresh_entries` (`memory/entries.py`), con `INSERT OR IGNORE` por `signal_group_key` | lecciones, calibración, API |
| `lessons` | lecciones agregadas por versión de reconstrucción (`build_id`) | `rebuild_lessons` | recuperación, API |
| `lesson_applications` | qué lecciones se inyectaron en cada decisión | `memory/retrieval.py` | API |
| `lessons_reports` | post-mortem de rachas perdedoras y tablas de calibración | `postmortem.py`, `calibration.py` | API, informes |

La memoria **lee resultados (PnL)** para aprender; nada de eso se muestra sin la puerta de resultados
(ver [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados)) y
`LESSONS_MEMORY_ENABLED` está apagado durante la medición, por lo que estas tablas están vacías en el
bot real.

## Qué las protege de cambios

### El disparador `llm_decision_inmutable`

El único disparador de la base. Sobre `shadow_trades`, **antes de actualizar `llm_decision`**: si la
etiqueta ya no es `SIN_LLM` y el valor nuevo es distinto, aborta con
`llm_decision es inmutable una vez decidida`. Es la segunda llave: `shadow_repo.set_llm_decision`
ya filtra con `WHERE llm_decision = 'SIN_LLM'`; el disparador lo garantiza también a nivel de base,
por si alguien escribe la columna con otro camino. No hay manera de "corregir" una etiqueta desde el
código: es deliberado, porque la medición compara etiquetas.

### Inmutabilidad por convención de código

| Tabla | Regla | Cómo se garantiza |
|---|---|---|
| `llm_logs` | solo `INSERT`, jamás `UPDATE`; una llamada por grupo | `signal_group_key UNIQUE`; el repositorio no tiene funciones de modificación |
| `memory_entries` | una fila por operación, no se reescribe | `INSERT OR IGNORE` por `signal_group_key UNIQUE` |
| `signals` | una fila por evaluación; solo `status` cambia (a `DISCARDED_SL_CAP`) | `UNIQUE (symbol, strategy, timeframe, candle_close_time)` más `INSERT OR IGNORE` |
| `paper_execution_log` | un grupo no abre dos veces | `signal_group_key UNIQUE`; reclamo con `INSERT OR IGNORE` |
| `real_arm_*` en `system_state` | se escriben una sola vez | el brazo solo las crea si no existen |

Estas reglas no son del esquema, son de los repositorios: cambiar un repositorio puede romperlas sin
que SQLite proteste. Las claves y el esquema de las tablas de la medición están en
[`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#esquema-de-las-tablas-de-la-medición).

## Migraciones

No hay números de versión ni una herramienta de migraciones: **el esquema es idempotente**.

1. `Database.connect()` ejecuta `executescript(SCHEMA)` en cada arranque. Todo es
   `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` y `CREATE TRIGGER IF NOT EXISTS`: una
   base existente solo recibe lo que le falta. **Una tabla nueva no necesita migración.**
2. `CREATE TABLE IF NOT EXISTS` no agrega columnas a una tabla que ya existe. Las columnas añadidas
   después de la primera versión de una tabla viven en un diccionario `_<TABLA>_MIGRATED_COLUMNS` y
   `_migrate_columns` las agrega con `ALTER TABLE ... ADD COLUMN` si no están (consultando
   `PRAGMA table_info`). Hoy hay cinco: `backtest_verdicts`, `trades`, `signals`, `shadow_trades` y
   `llm_logs`.
3. Todo ocurre bajo el candado de escritura, en la conexión de arranque, **antes** de que arranque
   ninguna tarea.

Consecuencias que conviene tener presentes:

- **Las columnas de un diccionario no están en el `CREATE TABLE` de `SCHEMA`.** Una base nueva las
  recibe también por el `ALTER` del primer arranque. Declarar la columna además en `SCHEMA` es
  opcional; lo que de verdad la agrega a una base vieja es el diccionario.
- **El orden de las columnas difiere entre una base nueva y una migrada.** Por eso nunca se accede
  por posición ni se escribe un `INSERT` sin lista de columnas.
- **No hay vuelta atrás.** No hay `DROP COLUMN` ni cambio de tipo. Una columna agregada se queda.
- **Una versión anterior del código sobre una base ya migrada funciona** si todas las columnas nuevas
  son nulas o tienen valor por defecto: las columnas extra se ignoran y los `INSERT` con lista de
  columnas no las nombran. Una columna `NOT NULL` sin `DEFAULT` rompería a la versión vieja (y SQLite
  no permite agregarla igualmente).

## Cómo agregar una columna sin romper una base existente

Ejemplo: una columna `nota` en `trades`.

1. **Elige un tipo y un valor por defecto compatibles con `ALTER TABLE ADD COLUMN`**: nula
   (`"TEXT"`) o `NOT NULL DEFAULT <constante>` (`"INTEGER NOT NULL DEFAULT 0"`). Nada que dependa de
   otra columna.
2. **Agrégala al diccionario** `_TRADES_MIGRATED_COLUMNS` en `app/persistence/database.py`. Si la
   tabla no tiene diccionario (hoy solo lo tienen las cinco de arriba) crea
   `_<TABLA>_MIGRATED_COLUMNS` y añade la llamada `await self._migrate_columns("<tabla>", ...)` en
   `init_schema`. Escribe un comentario con el motivo y la subfase.
3. **El modelo:** el campo en `app/persistence/models.py` con un valor por defecto
   (`nota: str | None = None`). Si la tabla tiene una sombra (`ShadowTrade` extiende `Trade`),
   decide si aplica.
4. **El mapeo:** añade `nota=row["nota"]` en la función `row_to_*` del repositorio
   (`trades_repo.row_to_trade`).
5. **Las escrituras:** si la columna se escribe al insertar, añádela a la lista de columnas y a los
   valores del `INSERT` (cuenta los `?`); si se actualiza después, una función `update_*` nueva.
   Cada escritura nueva lleva docstring con la sección `Efectos:` (lo exige el guardián de
   docstrings).
6. **Una prueba de migración** en el estilo de `tests/unit/test_database_migrations.py`: una base
   con el esquema anterior (la tabla sin la columna y con una fila), `Database(...).connect()`, y
   comprueba que la columna existe, que la fila vieja sobrevive con `NULL` y que una segunda conexión
   no falla.
7. **Regenera la documentación derivada:** `python scripts/export_schema_doc.py` (la prueba
   `tests/unit/test_schema_doc.py` falla si `docs/ESQUEMA_BD.md` no coincide) y
   `python scripts/build_dev_docs.py` (la referencia pdoc y los hechos de las guías).
8. **Comprueba contra una copia de la base real**, nunca contra la de trabajo (regla de CLAUDE.md):
   saca una copia consistente (`scripts/docker_db.py export` con el bot en Docker;
   `scripts/backup_db.py` con el bot nativo), ábrela con `Database(copia).connect()` dos veces y
   verifica que la columna existe, que `PRAGMA integrity_check` dice `ok` y que el recuento de filas de
   las demás tablas no cambió. Un ensayo con datos sintéticos no basta.
9. **Si la tabla es una de las de la medición** (`shadow_trades`, `llm_logs`, `signals`,
   `signals_discarded_by_sl_cap`, `asset_universe`): agregar una columna nula es aditivo y
   `scripts/check_measurement_safety.py` lo señala como `columna nueva (aditivo)`, nivel REVISAR; aun
   así, **ningún código del camino medido puede leerla ni escribirla**. Ver
   [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#esquema-de-las-tablas-de-la-medición).
10. **Despliegue:** la imagen nueva aplica el `ALTER TABLE` al primer arranque contra la base real. Es
    una operación de metadatos (SQLite no reescribe la tabla al añadir una columna con valor por
    defecto constante), pero el despliegue sigue la ventana segura de
    [`RECETAS.md`](RECETAS.md#desplegar-un-cambio-al-contenedor).

## Cómo agregar una tabla

1. **El `CREATE TABLE IF NOT EXISTS`** en `SCHEMA` (`app/persistence/database.py`), con un comentario
   antes que explique el propósito: la documentación generada lo usa como descripción. Si necesita
   índices, `CREATE INDEX IF NOT EXISTS`. **No hace falta migración**: el primer arranque la crea.
2. **Un grupo** para la tabla en `GROUPS` de `scripts/export_schema_doc.py` (una tabla sin grupo hace
   fallar la generación a propósito).
3. **El modelo y el repositorio:** un modelo pydantic en `models.py` (si hace falta) y un módulo
   `app/persistence/repositories/<nombre>_repo.py` con funciones `async` que reciben `db: Database`,
   un `row_to_*` si devuelven un modelo, y docstrings con `Args`, `Returns` y `Efectos:`.
4. **Restricciones que hagan falta:** `UNIQUE` y `INSERT OR IGNORE` si la escritura debe ser
   idempotente, `CHECK` para los valores válidos. No uses `FOREIGN KEY`: el proyecto no las usa.
5. **Pruebas** del repositorio (`tests/unit/test_<nombre>_repo.py`) y de la idempotencia.
6. **Regenera** `docs/ESQUEMA_BD.md` y la documentación técnica, como arriba.
7. **Confirma que no haya un tipo de bloqueo:** la tabla nueva debe escribirse con `Database.execute`
   (toma el candado), nunca con una conexión propia.

## Lo que no se hace

- **No renombres ni borres** una tabla o una columna existente: no hay migración que lo soporte y una
  versión anterior del código dejaría de funcionar.
- **No cambies el tipo ni una restricción** de una tabla existente: `CREATE TABLE IF NOT EXISTS` no
  actualiza una tabla que ya existe, así que el cambio no llegaría a la base real y los entornos
  divergirían en silencio.
- **No copies el archivo `.db` a mano** para respaldarlo: en modo WAL puede quedar inconsistente. Usa
  `scripts/docker_db.py` (con Docker) o `scripts/backup_db.py` / `scripts/restore_db.py`.
- **No abras la base real** con una herramienta nueva: los scripts que la leen aceptan `--db` y
  trabajan sobre una copia, abierta con `mode=ro` (ver [`modulos/scripts.md`](modulos/scripts.md)).
- **No guardes secretos ni resultados** que la API o Telegram puedan mostrar sin pasar por la puerta
  de resultados.

## Trampas conocidas

- **`SELECT *` más acceso por nombre:** si un `SELECT` nombra las columnas en vez de usar `*`, la
  función `row_to_*` fallará con `IndexError` en cuanto el modelo gane un campo que el `SELECT` no
  trae.
- **`INSERT OR IGNORE` oculta errores:** una violación de `UNIQUE` no lanza. Es lo que se quiere para
  la idempotencia, pero un `CHECK` fallido en el mismo `INSERT OR IGNORE` también se ignora.
- **Booleanos como 0/1:** el modelo usa `bool`, la columna `INTEGER`; el mapeo es manual
  (`bool(row["..."])` y `int(value)`).
- **Las fechas son texto:** compáralas con `ISO-8601` del mismo formato y zona (UTC); un `datetime`
  sin zona se compara mal. `candle_close_time` es texto en `signals` y `shadow_trades`, pero `open_time`
  de las velas es entero en milisegundos.
- **Las cachés de mercado son por serie:** (`symbol`, `interval`, `price_type`). El mismo símbolo en
  `LAST_PRICE` y `MARK_PRICE` son series distintas.
- **`asset_universe` es un historial de snapshots**, no una lista: el universo vigente son las filas
  `included = 1` del último `refreshed_at`.
- **Los scripts y las pruebas usan una base temporal.** El bot de desarrollo corre en el puerto 8001
  sobre una copia (`scripts/run_dev_server.py`); nunca apuntes `DATABASE_PATH` a `data/`.

## Lo no verificado

- No se midió cuánto tarda el `ALTER TABLE ADD COLUMN` sobre la base real (el razonamiento de que es
  una operación de metadatos es el comportamiento documentado de SQLite, no una medición en este
  equipo).
- Que una versión anterior del código funcione sobre una base ya migrada es una consecuencia del
  diseño (acceso por nombre, `INSERT` con lista de columnas), no una prueba automática.
