# Guía del paquete `persistence`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/persistence.html`](../api/app/persistence.html)

## Qué hace el paquete

La capa de datos: **una** conexión `aiosqlite` compartida por todo el proceso (`database.py`), el esquema SQL con sus migraciones de
columnas, los modelos pydantic de las filas (`models.py`) y **un repositorio por grupo de tablas** (`repositories/`). La regla de la
capa: **ningún otro paquete escribe SQL a mano en el flujo normal; todo pasa por un repositorio** (hay excepciones puntuales de solo
lectura en informes y scripts). Cómo se organiza cada tabla, quién la escribe y cómo agregar una columna o una tabla sin romper una
base existente está en [MODELO_DATOS](../MODELO_DATOS.md); el esquema tabla por tabla, en [ESQUEMA_BD](../../ESQUEMA_BD.md).

## Zona protegida

**Parcial.** Las **tablas de la medición** (`shadow_trades`, `llm_logs`, `signals`, `asset_universe`) y las garantías de su
diseño no se pueden relajar: la etiqueta inmutable (disparador `llm_decision_inmutable`), las claves únicas (`signal_group_key`) y el
`INSERT OR IGNORE`. **Agregar** una columna o una tabla nuevas es seguro (aditivo); **cambiar el significado** de una existente o
reescribir sus filas no. Ver [Sombras](../ZONAS_PROTEGIDAS.md#sombras) y
[Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición).

## Cómo se conecta

- `app/main.py` crea la `Database`, llama a `connect()` (que crea o completa el esquema) y la cuelga de `app.state`.
- Una sola conexión con **un candado de escritura**: `execute()` y `execute_many()` lo toman y hacen commit; `fetch_one()` y `fetch_all()`
  no lo toman. SQLite en modo WAL con `synchronous=NORMAL`.
- Los repositorios son módulos de funciones `async` que reciben la `Database` como primer argumento y devuelven modelos de `models.py`.

## Archivos

### `app/persistence/__init__.py`

**Responsabilidad.** Docstring del paquete y la regla de la capa.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/persistence/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence.html`](../api/app/persistence.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_build_dev_docs.py`, `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:app/persistence/__init__.py -->

### `app/persistence/database.py`

**Responsabilidad.** La conexión, el esquema completo (`SCHEMA`) y las migraciones de columnas.

**Funciones y clases clave.**
- `Database.connect()` — crea el directorio y el archivo si faltan, activa WAL y `synchronous=NORMAL`, y llama a `init_schema()`.
- `Database.init_schema()` — ejecuta `SCHEMA` (todo con `CREATE ... IF NOT EXISTS`) y después `_migrate_columns` para cada tabla con
  columnas agregadas más tarde (`backtest_verdicts`, `trades`, `signals`, `shadow_trades` y `llm_logs`). Idempotente: una base existente solo recibe lo que
  le falta.
- `Database.execute()`, `execute_many()` (una sola transacción: evita un fsync por fila), `fetch_one()` y `fetch_all()`.
- `SCHEMA` — el DDL: 28 tablas, índices y el disparador `llm_decision_inmutable`.

**Invariantes — no romper.**
- **El esquema solo crece.** `CREATE TABLE IF NOT EXISTS` no agrega columnas a una tabla vieja: por eso cada columna nueva de una tabla que ya
  existe en bases anteriores va en el diccionario `_*_MIGRATED_COLUMNS` de esa tabla (la migra con `ALTER TABLE ADD COLUMN`, sin borrar nada).
  Declararla también en `SCHEMA` es opcional.
- **Etiqueta inmutable:** el disparador `llm_decision_inmutable` aborta cualquier cambio de `shadow_trades.llm_decision` una vez decidida
  (`SIN_LLM` → `APROBADA`/`RECHAZADA` ocurre una sola vez).
- `signal_group_key` es `UNIQUE` en `shadow_trades`, `llm_logs`, `paper_execution_log`, `memory_entries` y `lesson_applications`: esa clave es el hilo
  que une una señal con su sombra, su etiqueta y su ejecución.
- Una sola conexión: no abrir otra sobre el mismo archivo desde el proceso del bot.

**Trampas conocidas.**
- Las columnas de `trades` posteriores a la primera versión (niveles, trailing, `decision_source`, slippage, `fill_source`, reconciliación…) **solo** están en
  `_TRADES_MIGRATED_COLUMNS`, no en el `CREATE TABLE`; una base nueva las recibe en el mismo `connect()`.
- `backtest_verdicts` conserva columnas de una versión anterior de la simulación de cartera «por compatibilidad» sin usarlas.
- `docs/ESQUEMA_BD.md` se **genera** de este archivo (`scripts/export_schema_doc.py`); `tests/unit/test_schema_doc.py` falla si queda desactualizado.

**Protección.** Parcial: ver arriba.

<!-- hechos:app/persistence/database.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/database.html`](../api/app/persistence/database.html)
- **Importa (internos):** ninguno
- **Lo importan:** 82 módulos (api 15, backtesting 2, core 1, execution 1, llm 5, main 1, market 4, memory 8, news 4, persistence 16, scripts 10, telegram 3, trading 12)
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** `asset_universe`, `backtest_runs`, `backtest_skipped_entries`, `backtest_trades`, `backtest_verdicts`, `contract_specs_cache`, `control_audit`, `data_source_health`, `fear_greed`, `funding_cache`, `lesson_applications`, `lessons`, `lessons_reports`, `llm_logs`, `memory_entries`, `news_fetch_state`, `news_items`, `ohlcv_cache`, `ohlcv_floor`, `ohlcv_series_complete`, `ohlcv_unrepairable_gaps`, `paper_execution_log`, `risk_rejections`, `shadow_trades`, `signals`, `signals_discarded_by_sl_cap`, `system_state`, `trades`
- **Pruebas que lo importan o citan:** 30 archivos (e2e 1, tests/ 1, unit 28)
<!-- /hechos:app/persistence/database.py -->

### `app/persistence/llm_log_fields.py`

**Responsabilidad.** Recuperar del prompt guardado los campos de `llm_logs` que el etiquetador en vivo no escribe.

**Funciones y clases clave.**
- `atr_from_prompt()` — `volatility_atr14_pct` del mensaje de usuario guardado en `prompt`; `atr_value()` — la columna `atr_pct` si está, y si no la del prompt.

**Invariantes — no romper.** Son funciones **puras**, de solo lectura: no cambian lo que se escribe, ni el prompt, ni las etiquetas.

**Trampas conocidas.** El etiquetador en vivo no pasa `atr_pct` a `decide_group`, así que esa columna queda `NULL` en las filas de `MEDICION`; el dato sigue
en `prompt`.

<!-- hechos:app/persistence/llm_log_fields.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/llm_log_fields.html`](../api/app/persistence/llm_log_fields.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.memory.entries`, `app.trading.ai_value_report`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_atr_from_prompt.py`
<!-- /hechos:app/persistence/llm_log_fields.py -->

### `app/persistence/models.py`

**Responsabilidad.** Los modelos pydantic que reflejan las filas: el resto del código trabaja con ellos y no con filas crudas.

**Funciones y clases clave.**
- `Side` (`LONG`/`SHORT`), `TradeStatus` (`OPEN`/`CLOSED`), `Trade` (cuenta paper), `ShadowTrade` (extiende `Trade`), `RiskRejection`, `SignalRecord`,
  `SignalDiscardedBySLCap`, `OHLCVBar`, `ContractSpec`, `AssetUniverseEntry` y los `Backtest*`.

**Invariantes — no romper.** `Trade` y `ShadowTrade` declaran `pnl_net_usdt` como campo real: por eso la API **no** usa `response_model` con ellos (el filtrado de
FastAPI no protegería nada).

**Trampas conocidas.** Agregar un campo a un modelo no lo expone en la API: las respuestas usan listas blancas.

<!-- hechos:app/persistence/models.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/models.html`](../api/app/persistence/models.html)
- **Importa (internos):** ninguno
- **Lo importan:** 43 módulos (api 5, backtesting 4, execution 4, llm 3, market 3, memory 1, persistence 8, telegram 2, trading 13)
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 57 archivos (e2e 10, unit 47)
<!-- /hechos:app/persistence/models.py -->

### `app/persistence/repositories/__init__.py`

**Responsabilidad.** Docstring: funciones `async` que reciben una `Database` abierta y devuelven modelos.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Las tablas que llevan resultados (PnL, tasas) no se exponen hacia fuera sin `is_outcome_reveal_allowed`.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/persistence/repositories/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories.html`](../api/app/persistence/repositories.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:app/persistence/repositories/__init__.py -->

### `app/persistence/repositories/backtest_repo.py`

**Responsabilidad.** Las tablas del backtest de la Fase 2: `backtest_trades`, `backtest_skipped_entries`, `backtest_runs` y `backtest_verdicts`.

**Funciones y clases clave.**
- `clear_results()` (borra los cuatro antes de una corrida completa), `insert_trades()`, `insert_skipped_entry()`, `insert_run()`, `insert_verdict()`,
  `get_verdicts()` y `get_trades()`.

**Invariantes — no romper.** Los `INSERT` generan los `?` a partir de la lista de columnas (`_insert`), nunca a mano; un `assert` detecta si `values` no tiene la misma
longitud que `columns`.

**Trampas conocidas.** *Error real:* «29 values for 30 columns» en `insert_verdict` al agregar la simulación de cartera Monte Carlo; `_insert` hace imposible ese desajuste.

<!-- hechos:app/persistence/repositories/backtest_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/backtest_repo.html`](../api/app/persistence/repositories/backtest_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.api.routes.backtest`, `app.backtesting.report`, `scripts.analyze_risk`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `backtest_trades`, `backtest_verdicts`
- **Tablas que escribe:** `backtest_runs`, `backtest_skipped_entries`, `backtest_trades`, `backtest_verdicts`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_analyze_risk_script.py`, `tests/unit/test_backtest_repo.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_run_full_backtest_smoke.py`
<!-- /hechos:app/persistence/repositories/backtest_repo.py -->

### `app/persistence/repositories/control_audit_repo.py`

**Responsabilidad.** El rastro de cada acción de control (`control_audit`).

**Funciones y clases clave.**
- `insert()`, `get_recent()` y `get_latest()`; `ControlAuditEntry`.

**Invariantes — no romper.** Solo guarda quién, cuándo, la acción y el estado de pausa y kill antes y después: **nunca** datos del chat ni resultados.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/control_audit_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/control_audit_repo.html`](../api/app/persistence/repositories/control_audit_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.repositories.timeutil`
- **Lo importan:** `app.api.routes.control`, `app.telegram.push`, `app.trading.control_actions`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `control_audit`
- **Tablas que escribe:** `control_audit`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_base.py`, `tests/unit/test_phase5_control_api.py`
<!-- /hechos:app/persistence/repositories/control_audit_repo.py -->

### `app/persistence/repositories/funding_repo.py`

**Responsabilidad.** La caché del historial de funding (`funding_cache`).

**Funciones y clases clave.**
- `upsert_funding()`, `get_funding()` y `get_covered_range()`.

**Invariantes.** El piso del historial de funding vive en `ohlcv_floor` con `interval = price_type = '__funding__'`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/funding_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/funding_repo.html`](../api/app/persistence/repositories/funding_repo.html)
- **Importa (internos):** `app.persistence.database`
- **Lo importan:** `app.backtesting.engine`, `app.llm.market_features`, `app.market.funding_history`, `app.trading.funding_accrual`, `app.trading.signal_generator`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `funding_cache`
- **Tablas que escribe:** `funding_cache`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_funding_contrarian_percentile_strategy.py`, `tests/unit/test_funding_history.py`, `tests/unit/test_llm_market_features.py`, `tests/unit/test_llm_prompts.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_signal_generator.py`, `tests/unit/test_slippage_and_funding_accrual.py`
<!-- /hechos:app/persistence/repositories/funding_repo.py -->

### `app/persistence/repositories/health_repo.py`

**Responsabilidad.** La salud de las fuentes de datos en vivo (`data_source_health`).

**Funciones y clases clave.**
- `record_success()`, `record_failure()`, `get_health_rows()`, `get_consecutive_failures()` y `get_last_success()`; `WS_FEED_SOURCE = 'ws_feed'`.

**Invariantes — no romper.** `ws_feed` es el latido del WebSocket público: sin él el motor de riesgo bloquea entradas nuevas. Las noticias usan `news:<id>`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/health_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/health_repo.html`](../api/app/persistence/repositories/health_repo.html)
- **Importa (internos):** `app.persistence.database`
- **Lo importan:** `app.news.collector`, `app.news.context`, `app.news.health`, `app.trading.position_monitor`, `app.trading.risk_engine`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `data_source_health`
- **Tablas que escribe:** `data_source_health`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_collector.py`, `tests/unit/test_news_context.py`, `tests/unit/test_news_status_script.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_risk_engine.py`
<!-- /hechos:app/persistence/repositories/health_repo.py -->

### `app/persistence/repositories/llm_logs_repo.py`

**Responsabilidad.** La tabla `llm_logs`: una fila por llamada al LLM sobre una señal agrupada, exitosa o no.

**Funciones y clases clave.**
- `insert()`, `get_by_signal_group_key()`, `get_recent()`, `get_spend_since()` (el gasto del día para el presupuesto), `get_status_counts_since()` y
  `get_first_occurrence_since()`.
- `get_measurement_signal_group_keys()` — los grupos de la medición (`fase = 'MEDICION'`) de **un** hash de prompt (por defecto el de la fila más reciente) y
  `get_piloto_signal_group_keys()` (excluidos de la medición).

**Invariantes — no romper.**
- `signal_group_key` es **única**: una sola llamada por grupo, sin reintentos.
- Dos versiones del prompt (congelación rota y corregida) no se mezclan en un mismo veredicto: la anterior queda como historial pero fuera de las claves de la medición.

**Trampas conocidas.** Una fila con `status != 'OK'` también entra en las claves de la medición: se intentó decidir y no se pudo, por eso cuenta como `SIN_LLM`.

**Protección.** Sí: [Claves de la medición](../ZONAS_PROTEGIDAS.md#claves-de-la-medición).

<!-- hechos:app/persistence/repositories/llm_logs_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/llm_logs_repo.html`](../api/app/persistence/repositories/llm_logs_repo.html)
- **Importa (internos):** `app.persistence.database`
- **Lo importan:** `app.api.routes.health`, `app.api.routes.llm_decisions`, `app.llm.budget`, `app.llm.decision_service`, `app.telegram.commands`, `app.trading.shadow_report`, `scripts.llm_pilot`, `scripts.llm_smoke`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `llm_logs`
- **Tablas que escribe:** `llm_logs`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_llm_budget.py`, `tests/unit/test_llm_decision_service.py`, `tests/unit/test_llm_logs_repo.py`, `tests/unit/test_measurement_status_script.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_prompt_v5_prepared.py`, `tests/unit/test_readonly_api_routes.py`, `tests/unit/test_shadow_live_labeler.py`, `tests/unit/test_telegram_commands.py`
<!-- /hechos:app/persistence/repositories/llm_logs_repo.py -->

### `app/persistence/repositories/memory_repo.py`

**Responsabilidad.** La memoria de lecciones: `memory_entries`, `lessons`, `lesson_applications` y `lessons_reports`.

**Funciones y clases clave.**
- `insert_entry()` (salvo que su grupo ya tenga una: son **inmutables**), `get_entries_closed_before()` (estrictamente antes de `cutoff`), `replace_lessons()` (una
  reconstrucción completa bajo un `build_id`), `get_lessons()`, `record_application()` y `save_report()`.
- `MemoryEntry` y `LessonRow`.

**Invariantes — no romper.** Las filas llevan PnL y tasas de acierto: **nada** de lo que lee este repositorio se muestra sin `is_outcome_reveal_allowed`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/memory_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/memory_repo.html`](../api/app/persistence/repositories/memory_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.repositories.timeutil`
- **Lo importan:** `app.api.routes.memory`, `app.memory.calibration`, `app.memory.entries`, `app.memory.features`, `app.memory.lessons`, `app.memory.postmortem`, `app.memory.report`, `app.memory.retrieval`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `lesson_applications`, `lessons`, `lessons_reports`, `memory_entries`
- **Tablas que escribe:** `lesson_applications`, `lessons`, `lessons_reports`, `memory_entries`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_phase4_base.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/persistence/repositories/memory_repo.py -->

### `app/persistence/repositories/news_repo.py`

**Responsabilidad.** Las noticias: `news_items`, `news_fetch_state` y `fear_greed`.

**Funciones y clases clave.**
- `insert_item()` (salvo que ya exista por `url_hash`), `get_for_context()` (noticias no duplicadas que existían en `as_of`), `get_recent()`,
  `prune_older_than()`, `get_state()`/`save_state()` y `upsert_fear_greed()`/`latest_fear_greed()`.

**Invariantes — no romper.** Todo lo que se lee para un contexto de decisión exige `fetched_at <= instante` **y** `published_at <= instante`: lo que el bot todavía no había
visto no existía para esa decisión. Solo título, URL, dominio y fecha (nunca el cuerpo).

**Trampas conocidas.** Los titulares son texto **no confiable**.

<!-- hechos:app/persistence/repositories/news_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/news_repo.html`](../api/app/persistence/repositories/news_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.repositories.timeutil`
- **Lo importan:** `app.api.routes.news`, `app.memory.entries`, `app.news.collector`, `app.news.context`, `app.news.health`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `fear_greed`, `news_fetch_state`, `news_items`
- **Tablas que escribe:** `fear_greed`, `news_fetch_state`, `news_items`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_news_collector.py`, `tests/unit/test_news_context.py`, `tests/unit/test_news_status_script.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_phase4_base.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/persistence/repositories/news_repo.py -->

### `app/persistence/repositories/ohlcv_repo.py`

**Responsabilidad.** La caché de velas y sus marcas de integridad: `ohlcv_cache`, `ohlcv_floor`, `ohlcv_series_complete` y `ohlcv_unrepairable_gaps`.

**Funciones y clases clave.**
- `upsert_bars()` (un lote en una transacción), `get_bars()`, `get_covered_range()`, `get_floor()`/`set_floor()` (conserva el más antiguo),
  `mark_series_complete()` y `mark_unrepairable_gap()`/`clear_unrepairable_gap()`.

**Invariantes — no romper.** Permite paginar la descarga (máximo 200 velas por llamada, 10 pedidos por segundo) sin volver a pedir velas ya guardadas.
`ohlcv_series_complete` es una marca **afirmativa** que escribe `scripts/download_history.py` solo si llegó al final de la serie sin errores.

**Trampas conocidas.** Sin `ohlcv_series_complete`, un `ohlcv_floor` ausente (por una descarga interrumpida) causaba un `MissingHistoricalDataError` evitable.

<!-- hechos:app/persistence/repositories/ohlcv_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/ohlcv_repo.html`](../api/app/persistence/repositories/ohlcv_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.llm.market_features`, `app.market.funding_history`, `app.market.ohlcv_history`, `app.trading.position_monitor`, `app.trading.signal_generator`, `scripts.download_history`, `scripts.repair_ohlcv_gaps`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `ohlcv_cache`, `ohlcv_floor`, `ohlcv_series_complete`, `ohlcv_unrepairable_gaps`
- **Tablas que escribe:** `ohlcv_cache`, `ohlcv_floor`, `ohlcv_series_complete`, `ohlcv_unrepairable_gaps`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 14 archivos (e2e 2, unit 12)
<!-- /hechos:app/persistence/repositories/ohlcv_repo.py -->

### `app/persistence/repositories/paper_execution_repo.py`

**Responsabilidad.** La tabla `paper_execution_log`: lo que decidió el brazo ejecutado.

**Funciones y clases clave.**
- `claim()` — reclama un grupo (`PENDING`): solo el primero que llega lo consigue; `resolve_opened()`, `resolve_skipped()`, `get_pending()`, `get_recent()`,
  `count_outcomes_since()` y `count_by_reason()`.

**Invariantes — no romper.** Solo guarda la decisión de ejecución y sus motivos, **nunca PnL**. El reclamo es idempotente (`signal_group_key` única).

**Trampas conocidas.** `PENDING` solo dura el instante entre el reclamo y la apertura (o hasta que el arranque lo resuelve).

<!-- hechos:app/persistence/repositories/paper_execution_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/paper_execution_repo.html`](../api/app/persistence/repositories/paper_execution_repo.html)
- **Importa (internos):** `app.persistence.database`
- **Lo importan:** `app.api.routes.arm`, `app.trading.paper_execution`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `paper_execution_log`
- **Tablas que escribe:** `paper_execution_log`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_restart.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_paper_execution_base.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_paper_execution_status_script.py`, `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/persistence/repositories/paper_execution_repo.py -->

### `app/persistence/repositories/risk_rejections_repo.py`

**Responsabilidad.** La auditoría de los rechazos del motor de riesgo (`risk_rejections`).

**Funciones y clases clave.**
- `insert_rejection()`, `get_rejections()` y `count_by_reason_since()` (solo conteos: no lee los detalles).

**Invariantes — no romper.** Nunca se usa para cierres. Los detalles pueden traer el equity: por eso la API no los expone.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/risk_rejections_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/risk_rejections_repo.html`](../api/app/persistence/repositories/risk_rejections_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.api.routes.risk`, `app.trading.risk_engine`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `risk_rejections`
- **Tablas que escribe:** `risk_rejections`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_execution.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_risk_engine.py`
<!-- /hechos:app/persistence/repositories/risk_rejections_repo.py -->

### `app/persistence/repositories/shadow_repo.py`

**Responsabilidad.** La tabla `shadow_trades`: de ella sale la medición del valor del LLM.

**Funciones y clases clave.**
- `insert_if_new()` (`INSERT OR IGNORE` por `signal_group_key`), `get_open()`, `get_closed()`, `get_recent()`, `close()`, `update_risk_state()`,
  `update_funding()` y `update_reconciliation_state()`.
- `set_llm_decision()` — escribe la etiqueta **una sola vez** (`SIN_LLM` → `APROBADA` o `RECHAZADA`); `get_unreliable_signal_group_keys()` — los
  grupos cuya reconciliación falló, que el veredicto excluye; `mark_executed()`.

**Invariantes — no romper.** La etiqueta es inmutable: lo garantizan `set_llm_decision` (filtra por `WHERE llm_decision = 'SIN_LLM'`) **y** el disparador de la base.

**Trampas conocidas.** `get_closed()` no tiene límite ni orden; `mark_executed` solo se llama en la ruta `AUTO_OPEN_WITHOUT_LLM`.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/persistence/repositories/shadow_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/shadow_repo.html`](../api/app/persistence/repositories/shadow_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.api.routes.llm_decisions`, `app.api.routes.shadow_trades`, `app.core.scheduler`, `app.llm.decision_service`, `app.memory.entries`, `app.trading.ai_value_report`, `app.trading.position_monitor`, `app.trading.shadow_book`, `app.trading.shadow_live_labeler`, `app.trading.shadow_report`, `scripts.llm_pilot`, `scripts.llm_smoke`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `shadow_trades`
- **Tablas que escribe:** `shadow_trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_dev_docs_facts.py`, `tests/unit/test_llm_decision_service.py`, `tests/unit/test_measurement_status_script.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_readonly_api_routes.py`, `tests/unit/test_shadow_book.py`, `tests/unit/test_shadow_live_labeler.py`
<!-- /hechos:app/persistence/repositories/shadow_repo.py -->

### `app/persistence/repositories/signals_repo.py`

**Responsabilidad.** `signals` y `signals_discarded_by_sl_cap`: cada evaluación de una estrategia sobre un símbolo, timeframe y vela cerrada.

**Funciones y clases clave.**
- `insert_signal()` — salvo que esa vela ya se haya evaluado (`UNIQUE` + `INSERT OR IGNORE`); `update_status()`, `get_by_symbol_strategy_candle()`,
  `get_signals()`, `insert_discarded_by_sl_cap()` y `get_discarded_by_sl_cap()`.

**Invariantes — no romper.** Una fila por (símbolo, estrategia, timeframe, vela), HOLD incluido: registro auditable completo.

**Trampas conocidas.** Una señal descartada por el tope de SL **no se borra** de `signals`: queda con `status='DISCARDED_SL_CAP'`.

<!-- hechos:app/persistence/repositories/signals_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/signals_repo.html`](../api/app/persistence/repositories/signals_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.llm.prompts`, `app.memory.entries`, `app.trading.signal_generator`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `signals`, `signals_discarded_by_sl_cap`
- **Tablas que escribe:** `signals`, `signals_discarded_by_sl_cap`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_llm_prompts.py`, `tests/unit/test_prompt_v5_prepared.py`, `tests/unit/test_signal_generator.py`, `tests/unit/test_signals_repo.py`
<!-- /hechos:app/persistence/repositories/signals_repo.py -->

### `app/persistence/repositories/specs_repo.py`

**Responsabilidad.** La caché de especificaciones de contrato (`contract_specs_cache`).

**Funciones y clases clave.**
- `upsert_spec()` y `get_spec()`.

**Invariantes.** Una fila por símbolo; la alimenta `app/market/contract_specs.py` y la leen el simulador y el brazo ejecutado.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/specs_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/specs_repo.html`](../api/app/persistence/repositories/specs_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.backtesting.engine`, `app.execution.paper_backend`, `app.llm.market_features`, `app.market.contract_specs`, `app.trading.paper_execution`, `app.trading.position_monitor`, `app.trading.shadow_book`, `app.trading.signal_generator`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `contract_specs_cache`
- **Tablas que escribe:** `contract_specs_cache`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_llm_market_features.py`, `tests/unit/test_paper_backend.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_signal_generator.py`
<!-- /hechos:app/persistence/repositories/specs_repo.py -->

### `app/persistence/repositories/system_state_repo.py`

**Responsabilidad.** La tabla `system_state`: pares clave/valor con el estado del bot.

**Funciones y clases clave.**
- `set_state()` (inserta o reemplaza y actualiza `updated_at`) y `get_state()`.

**Invariantes — no romper.** Las claves las definen quienes las usan, no esta capa: pausa del scheduler, kill switch, circuit breaker, stop por drawdown y su contador,
las marcas `real_arm_*`, el estado del push, `monitor_last_seen_ms`, `signals_stale_data_count` y `universe_last_refresh_error`, entre otras.

**Trampas conocidas.** Un valor es siempre texto.

<!-- hechos:app/persistence/repositories/system_state_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/system_state_repo.html`](../api/app/persistence/repositories/system_state_repo.html)
- **Importa (internos):** `app.persistence.database`
- **Lo importan:** `app.api.routes.universe`, `app.market.universe`, `app.telegram.commands`, `app.telegram.push`, `app.trading.control_state`, `app.trading.paper_execution`, `app.trading.position_monitor`, `app.trading.risk_engine`, `app.trading.signal_generator`, `scripts.resume_drawdown_stop`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `system_state`
- **Tablas que escribe:** `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 13 archivos (e2e 1, unit 12)
<!-- /hechos:app/persistence/repositories/system_state_repo.py -->

### `app/persistence/repositories/timeutil.py`

**Responsabilidad.** Marcas de tiempo comparables como texto.

**Funciones y clases clave.**
- `iso_utc()` — un `datetime` como texto ISO 8601 **en UTC** (uno sin zona se toma como UTC).

**Invariantes — no romper.** Las columnas de tiempo son texto ISO: ordenar y comparar con `<=` solo es correcto si todas llevan el **mismo desfase**. Todo lo que se escribe o se
compara contra ellas pasa por `iso_utc`, para que nunca se abra una fuga hacia el futuro en los filtros «solo lo que existía en ese instante».

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/persistence/repositories/timeutil.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/timeutil.html`](../api/app/persistence/repositories/timeutil.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.persistence.repositories.control_audit_repo`, `app.persistence.repositories.memory_repo`, `app.persistence.repositories.news_repo`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/persistence/repositories/timeutil.py -->

### `app/persistence/repositories/trades_repo.py`

**Responsabilidad.** La tabla `trades` (la cuenta paper).

**Funciones y clases clave.**
- `create_trade()`, `close_trade()`, `update_risk_state()`, `update_funding()`, `update_reconciliation_state()` y `update_liq_price()`.
- `get_open_positions()`, `get_open_positions_as_of()` (abiertas en un instante; la usan las *features* del LLM), `get_trades()`, `get_closed_trades()`,
  `count_closed()` (solo un conteo: no lee PnL) y `committed_margin()`.

**Invariantes — no romper.** Cada función que escribe hace su propio commit a través de `Database.execute`. Lo usan `PaperBackend`, el monitor, el motor de riesgo y la API.

**Trampas conocidas.** Los ids de `trades` y de `shadow_trades` son independientes.

<!-- hechos:app/persistence/repositories/trades_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/trades_repo.html`](../api/app/persistence/repositories/trades_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** 14 módulos (api 4, core 1, execution 1, llm 1, telegram 2, trading 5)
- **Configuración que lee:** ninguno
- **Tablas que lee:** `trades`
- **Tablas que escribe:** `trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 19 archivos (unit 19)
<!-- /hechos:app/persistence/repositories/trades_repo.py -->

### `app/persistence/repositories/universe_repo.py`

**Responsabilidad.** Los snapshots del universo dinámico (`asset_universe`).

**Funciones y clases clave.**
- `insert_snapshot()` (un commit), `get_latest_snapshot()`, `get_latest_refreshed_at()`, `get_included_symbols()` y `get_included_history()`.

**Invariantes — no romper.** Cada refresco guarda **una fila por moneda evaluada** (incluidas las excluidas) con la misma marca `refreshed_at`; el universo vigente es el snapshot
más reciente.

**Trampas conocidas.** Ninguna conocida.

**Protección.** Sí: [Universo](../ZONAS_PROTEGIDAS.md#universo).

<!-- hechos:app/persistence/repositories/universe_repo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/persistence/repositories/universe_repo.html`](../api/app/persistence/repositories/universe_repo.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`
- **Lo importan:** `app.api.routes.universe`, `app.core.scheduler`, `app.market.universe`, `app.news.collector`, `app.telegram.push`, `app.trading.signal_generator`, `scripts.run_backtest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `asset_universe`
- **Tablas que escribe:** `asset_universe`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_phase5_push.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_signal_generator.py`, `tests/unit/test_universe_refresh.py`
<!-- /hechos:app/persistence/repositories/universe_repo.py -->
