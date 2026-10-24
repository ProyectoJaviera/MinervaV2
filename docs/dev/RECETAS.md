# Recetas

Paso a paso de los cambios más comunes, con los archivos exactos que hay que tocar, las pruebas que lo
exigen y qué se rompe si te olvidas de uno. Cada receta empieza por **si se puede hacer mientras la
medición está en curso**: la respuesta completa está en [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md).

| Receta | ¿Durante la medición? |
|---|---|
| [Agregar una estrategia](#agregar-una-estrategia) | **No** (zona protegida) |
| [Agregar un indicador](#agregar-un-indicador) | Sí, si ninguna estrategia vigente lo usa |
| [Agregar un comando de Telegram](#agregar-un-comando-de-telegram) | Sí, de solo lectura y con la puerta de resultados |
| [Agregar un endpoint de la API](#agregar-un-endpoint-de-la-api) | Sí, con la lista blanca y la puerta de resultados |
| [Agregar una pestaña del dashboard](#agregar-una-pestaña-del-dashboard) | Sí |
| [Agregar una variable de configuración](#agregar-una-variable-de-configuración) | Sí, si no cambia lo medido |
| [Agregar una fuente de noticias](#agregar-una-fuente-de-noticias) | Sí (no entra al prompt congelado) |
| [Cambiar un parámetro de riesgo](#cambiar-un-parámetro-de-riesgo) | Sí para la cuenta paper; **no** `LIVE_SL_MARGIN_CAP_PCT` |
| [Agregar un script de operación](#agregar-un-script-de-operación) | Sí |
| [Agregar una pieza opcional aislada](#agregar-una-pieza-opcional-aislada) | Sí, apagada por defecto |
| [Desplegar un cambio al contenedor](#desplegar-un-cambio-al-contenedor) | En la ventana segura |

## Antes de cualquier receta

1. **Corre `python scripts/check_measurement_safety.py`** cuando termines (o `--staged`): dice si tocaste
   una zona protegida. Si imprime `PROTEGIDO`, detente y lee [la zona
   correspondiente](ZONAS_PROTEGIDAS.md#mapa-rápido).
2. **Escribe la prueba primero o a la vez.** Las funciones de riesgo y de simulación necesitan casos límite
   ([`PRUEBAS.md`](PRUEBAS.md#cómo-escribir-una-prueba-nueva)).
3. **Todo código público nuevo lleva docstring** con `Args`, `Returns`, `Raises` y `Efectos:` según lo que
   haga ([formato](ENTORNO_DESARROLLO.md#el-formato-de-los-docstrings)); un guardián lo exige.
4. **Un archivo nuevo en `app/` o `scripts/` necesita su sección** en la guía del paquete: corre
   `python scripts/build_dev_docs.py` y escribe lo que quede `_Pendiente de documentar._`.
5. **Termina con** `python -m ruff check .`, las pruebas que tocaste y, antes de dar algo por terminado, la
   suite completa. Si tocaste el frontend, `npm --prefix frontend run test` y `run build`.

## Agregar una estrategia

> **No durante la medición.** Las seis estrategias y sus parámetros están pre-registrados: agregar,
> quitar o cambiar una cambia qué señales nacen. Además, el prompt congelado **describe las estrategias
> por nombre** (`app/llm/prompts.py`), así que una estrategia nueva llegaría al modelo sin que su texto la
> explique, y ese texto no puede cambiar. Es una medición nueva: ver
> [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#si-de-verdad-hay-que-cambiar-algo-protegido).

Para probarla sin registrarla: `app/backtesting/engine.py::run_backtest` recibe **una instancia** de
`BaseStrategy`, así que puedes usarla desde un script o una prueba sin tocar el registro (el backtest que
corre `scripts/run_backtest.py` y el generador de señales en vivo leen el **mismo** registro, no hay uno "solo
de backtest").

1. **La clase**, en `app/strategies/<nombre>.py`, que hereda de `BaseStrategy` (`app/strategies/base.py`):
   - `name` único, estable: es la clave del registro y de las tablas (`signals.strategy`,
     `shadow_trades.strategy`, `llm_logs`...). **No lo renombres después.**
   - `evaluate(df) -> Signal` decide `LONG`, `SHORT` o `HOLD` sobre la **última vela cerrada**; con menos
     velas de las que necesita, `HOLD`.
   - `precompute(df)` (opcional) agrega las columnas de indicadores una vez sobre toda la serie, y
     `evaluate` las usa si están. **Todos los indicadores deben ser causales**: el valor de una vela no
     puede depender de una posterior; si no, el backtest ve el futuro.
   - `stop_price`, `take_profit_price` y `trailing_distance` (opcionales): devuelven un precio absoluto o
     `None`, y con `None` el motor usa el SL y TP porcentuales de respaldo (`BACKTEST_FALLBACK_SL_PCT`,
     `BACKTEST_FALLBACK_TP_PCT`).
2. **El registro** `app/strategies/registry.py`: la instancia en `STRATEGIES`, sus timeframes en
   `STRATEGY_TIMEFRAMES` y, si no cuenta para el veredicto de descarte del backtest, su nombre en
   `EXPERIMENTAL_STRATEGIES`.
3. **El generador de señales** (`app/trading/signal_generator.py`):
   - si necesita más velas que las 300 por defecto, `_HISTORY_BARS_OVERRIDES`;
   - si lee la columna `funding_rate`, agrégala a `_FUNDING_STRATEGIES` (así se refresca el funding y se
     bloquea con `FUNDING_STALE` cuando no hay datos frescos).
4. **La elegibilidad** en la cuenta paper: `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` (vacío = todas). Ojo: esa lista
   también decide la **estrategia representante** de un grupo y, con ella, los niveles de la sombra.
5. **Las pruebas**, `tests/unit/test_<nombre>_strategy.py` con el estilo de `test_ema_cross_strategy.py`: la
   validación de parámetros, `HOLD` con pocos datos, un cruce alcista, uno bajista y la ausencia de cruce,
   con series construidas a mano y la cuenta verificada.
6. **El historial:** las velas del nuevo timeframe tienen que estar en la caché antes del backtest
   (`scripts/download_history.py`).
7. **La documentación:** [`../METODOLOGIA.md`](../METODOLOGIA.md) y la guía [`modulos/strategies.md`](modulos/strategies.md);
   la tabla de estrategias de `ZONAS_PROTEGIDAS.md`.

## Agregar un indicador

Los indicadores viven en `app/indicators/engine.py` como funciones puras sobre `pandas`
(`ema`, `sma`, `rsi`, `atr`, `bollinger_bands`, `donchian_channel`). Es "pandas puro" a propósito: no se
usa `pandas-ta`.

1. **La función**, con un docstring que diga qué calcula y su ventana. Valida los parámetros (`period <= 0`
   lanza `ValueError`, como las demás) y las columnas que necesita.
2. **Causal siempre:** el valor en la posición `i` solo puede usar las posiciones `<= i`.
3. **Las primeras posiciones:** `NaN` hasta que haya datos suficientes (como `sma`), o la convención de la
   definición (`ema` con `adjust=False`); documéntalo.
4. **Pruebas** en `tests/unit/test_indicators.py`: parámetro inválido, serie constante, tendencia sostenida,
   serie mínima y el valor numérico verificado a mano.
5. Solo si una estrategia vigente cambia de indicador se toca la medición; **un indicador nuevo que nadie
   usa no cambia nada.** `app/indicators/engine.py` está en la zona
   [Estrategias y parámetros](ZONAS_PROTEGIDAS.md#estrategias-y-parámetros): el script lo marcará como
   protegido aunque solo agregues una función; el criterio es que ninguna estrategia vigente la use.

## Agregar un comando de Telegram

Los comandos viven en `app/telegram/commands.py`. El bot solo atiende **un chat privado** (el de
`TELEGRAM_CHAT_ID`) y descarta lo demás; cada comando tiene un tope de 20 segundos.

1. **El manejador:** `async def cmd_<nombre>(ctx: BotContext) -> str`. El texto de respuesta empieza con
   `HEADER` (`MODO PAPER TRADING`). `ctx` trae `db`, `settings`, `paper_backend`, `scheduler`,
   `live_labeler`, `monitor` y `paper_arm`.
2. **Regístralo:** agrégalo a la tupla `COMMANDS` y al diccionario `READ_ONLY` (`{"/nombre": cmd_nombre}`).
   `help_text()` lista `COMMANDS`.
3. **Si muestra datos de operaciones** (PnL, saldo, precios de salida, motivos de cierre) debe consultar la
   puerta con `is_outcome_reveal_allowed(await compute_status_async(ctx.db))` y, cerrada, escribir
   `HIDDEN_RESULTS` en vez del dato. Mira `cmd_balance` y `cmd_metrics`. Un comando nuevo que se olvide de
   esto rompe [la promesa de los resultados ocultos](ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados).
4. **Solo lectura.** `/pause`, `/resume` y `/kill` son los únicos que escriben y llevan confirmación de dos
   pasos (`SI` en 60 s) y rastro en `control_audit`. Un comando de control nuevo no es una receta: la
   columna `control_audit.action` tiene un `CHECK` que solo admite esas tres acciones, y cambiar un `CHECK`
   obliga a reconstruir la tabla ([`MODELO_DATOS.md`](MODELO_DATOS.md#lo-que-no-se-hace)).
5. **Las pruebas:** `tests/unit/test_telegram_commands.py` (con el fixture `reveal_open` para el modo
   revelado) y, si muestra datos, agrega el comando a `TELEGRAM_COMMANDS` de `tests/e2e/leaks.py` para que
   el barrido de fugas lo recorra.
6. **La documentación pública:** la fila del comando en la tabla de [`../USO.md`](../USO.md#telegram). Una
   prueba (`test_documented_telegram_commands_are_the_real_ones`) exige que esa tabla y `COMMANDS` coincidan.
7. **Texto plano, sin `parse_mode`:** los mensajes salen sin formato (lo dice el docstring de
   `commands.py`), así que no hace falta escapar nada. Si alguna vez usas Markdown de Telegram, tendrás que
   escapar todo lo que venga de la base (símbolos, estrategias, motivos).

## Agregar un endpoint de la API

Las rutas viven en `app/api/routes/<tema>.py`, una por módulo, y `app/main.py` las registra. El dashboard es
el único consumidor.

1. **El router:** `router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])`. La dependencia
   **a nivel de router** es lo que protege por construcción: una ruta nueva queda con login sin que te
   acuerdes. Solo `GET /health`, `GET /trades` y `GET /positions` son públicos (y los dos últimos ocultan
   resultados); el login vive en `auth.py`.
2. **La función de la ruta:** recibe `db: Database = Depends(get_db)` y los demás objetos por las
   dependencias de `app/api/deps.py` (`get_settings`, `get_paper_arm`, `get_monitor`...). Si necesitas un objeto
   nuevo, cuélgalo de `app.state` en el `lifespan` y agrega su `get_*`.
3. **La respuesta es un `dict` o una lista de `dict`, nunca un `response_model` que contenga campos de
   resultado** (`pnl_net_usdt`...): FastAPI filtra por el modelo pero el valor real se serializaría igual. Todo
   lo que salga de `shadow_trades` pasa por `shadow_trade_to_public_dict(trade, reveal_outcome=...)`, con
   `reveal_outcome = is_outcome_reveal_allowed(await compute_status_async(db))`. Un campo nuevo no sale hasta
   agregarlo **a mano** a la lista blanca de `app/api/serializers.py`.
4. **Registra el router** en `app/main.py` (`app.include_router(<módulo>.router)`) y agrégalo al `import`.
5. **Las pruebas:** una en `tests/unit/` con `TestClient` o `httpx.ASGITransport` (mira
   `test_phase5_read_api.py`), con y sin sesión (`401`), y con la puerta cerrada y abierta si toca resultados.
   Agrega la ruta a `JSON_ROUTES_WITH_SESSION` de `tests/e2e/leaks.py` para el barrido de fugas.
6. **El conteo de rutas:** `test_the_api_has_the_number_of_routes_the_documents_state` exige exactamente 22
   rutas y que las únicas que escriben sean `POST /api/auth/login`, `POST /api/auth/logout` y
   `POST /api/control/{}`. Si agregas una ruta de lectura, sube el número en esa prueba **y** en
   [`../ARQUITECTURA.md`](../ARQUITECTURA.md) y [`../INFORME_FINAL.md`](../INFORME_FINAL.md), que lo citan. Una
   ruta que **escribe** no es una receta: cambia el contrato de seguridad de la API (login, mismo origen, JSON,
   palabra de confirmación y auditoría) y se discute antes.
7. **La documentación:** si la citas en un documento (entre comillas invertidas, con el método y la ruta
   completa, p. ej. `GET /api/risk`), la prueba `test_documented_api_routes_exist` verifica que existe.
8. **El tipo del frontend:** `frontend/src/api.ts` refleja **exactamente** la forma de la respuesta; actualízalo
   (ver la receta siguiente).

## Agregar una pestaña del dashboard

El dashboard está en `frontend/src/`: React 18, TypeScript, React Query 5 y Tailwind 4, con Vite 5 y Vitest.
Primero el endpoint (receta anterior).

1. **El tipo y el hook** en `frontend/src/api.ts`: la interfaz con la forma exacta de la respuesta y un hook de
   React Query con su `queryKey`:

   ```ts
   export function useMiVista() {
     return useQuery({
       queryKey: ["mi-vista"],
       queryFn: () => apiFetch<MiVistaResponse>("/api/mi-vista"),
     });
   }
   ```

   `apiFetch` incluye la cookie de sesión y, ante un `401`, avisa al componente raíz para volver al login.
2. **El componente** `frontend/src/<Nombre>Tab.tsx` (aquí `MiVistaTab.tsx`): usa `Card`, `StatTile` y `Unavailable` de
   `frontend/src/components/`, y `fmtDate` de `format.ts`. Maneja `isLoading` y el error (`Unavailable`).
3. **Regla de oro del dashboard:** nunca calcules ni muestres un resultado (PnL, precio de salida, motivo de
   cierre) que el servidor no mande; falla cerrado. `SummaryTab.tsx` solo muestra las columnas de resultado si
   `outcome_reveal_allowed === true`.
4. **La pestaña:** en `frontend/src/DashboardView.tsx`, una entrada en `TABS` (`id` solo con letras
   minúsculas, `label` en español) y un `case` en `ActiveTab`. Cada pestaña pide sus datos **solo cuando se
   abre**.
5. **Las pruebas de Vitest:** `MiVistaTab.test.tsx` con `mockFetch` y `withQueryClient` de `testUtils.tsx`
   (una ruta no mockeada lanza, como un servidor sin esa función). Actualiza
   `DashboardView.test.tsx`, que **fija la lista de pestañas** (`ofrece las cinco secciones y abre en Resumen`).
6. **La documentación pública:** la fila de la pestaña en la tabla de [`../USO.md`](../USO.md#el-dashboard),
   **en el mismo orden** que `TABS` (`test_documented_dashboard_tabs_are_the_real_ones` lo exige), y el
   texto "cinco pestañas".
7. **Construir:** `npm --prefix frontend run test` y `npm --prefix frontend run build`. La imagen Docker
   compila el frontend con `npm ci` y el `package-lock.json`: si agregas una dependencia de npm, actualiza el
   lockfile.
8. **La guía:** `python scripts/build_dev_docs.py` agrega la sección de `MiVistaTab.tsx` en
   [`modulos/frontend.md`](modulos/frontend.md).

## Agregar una variable de configuración

1. **El campo**, en `app/config.py`, en la sección que corresponda y con un comentario que explique para qué
   y por qué ese valor por defecto:

   ```python
   mi_variable: int = Field(default=10, alias="MI_VARIABLE")
   ```

   - El nombre de la variable de entorno es el `alias`. Una credencial **nunca** tiene un valor por defecto
     que no sea la cadena vacía.
   - Un campo `*_pct` necesita decidir su escala: **fracción 0-1** o **porcentaje 0-100**
     ([`../CONFIGURACION.md`](../CONFIGURACION.md#unidades-fracciones-y-porcentajes)). Si controla un límite de
     riesgo en vivo, agrégalo a `_FRACTION_PCT_FIELDS` o `_PERCENT_PCT_FIELDS` para que el bot falle al
     arrancar con un valor fuera de escala.
2. **`.env.example`:** la línea `MI_VARIABLE=10`. `test_env_example.py` exige que estén **todas** las
   variables de `Settings` (ni una más ni una menos) y que copiar la plantilla dé exactamente los valores por
   defecto del código.
3. **[`../CONFIGURACION.md`](../CONFIGURACION.md):** una fila en la tabla de su sección, con cuatro columnas
   (variable, qué hace, por defecto, cuándo cambiarla). Una prueba exige que cada variable esté descrita,
   que no se describan variables que no existen y que el valor por defecto de la tabla coincida con el del
   código.
4. **Léela con `settings.mi_variable`** (la instancia `settings` de `app.config`) o recibe un `Settings`
   como parámetro, que es lo que permite probar con otro valor. Nunca leas `os.environ` directamente.
5. **Si es un interruptor** que enciende una pieza opcional: apagado por defecto, fijado por `docker-compose.yml`
   y `scripts/start_bot.ps1` para el bot real (`test_docker_setup.py` verifica la paridad entre los dos),
   forzado a `false` en `scripts/run_dev_server.py`, y descrito en la tabla "Quién fija cada interruptor" de
   CONFIGURACION.md.
6. **¿Cambia lo medido?** Si afecta a las señales, las sombras, el etiquetado o su significado, es una variable
   **protegida**: agrégala a `ZONES` en `scripts/check_measurement_safety.py` (en `config` de su zona) y a la
   tabla de [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#parámetros-de-configuración-protegidos); dos pruebas
   comprueban que la lista, la tabla y el código coinciden.
7. **Pruébala** con `Settings(_env_file=None, MI_VARIABLE=20)`.
8. **Regenera la documentación técnica** (`python scripts/build_dev_docs.py`): las guías listan la
   configuración que lee cada archivo.
9. **Desplegar:** el contenedor lee el `.env` al crearse; si solo cambió el `.env` o el compose, basta
   `docker compose up -d --force-recreate` (ver [el despliegue](#desplegar-un-cambio-al-contenedor)).

## Agregar una fuente de noticias

> Las noticias son **texto no confiable** y no entran al prompt congelado. La recolección es una tarea
> aislada, apagada por defecto y sin costo de la API de Anthropic.

**Antes de escribir código, verifica la fuente en su documentación oficial** (regla de costos del proyecto):
que tenga un plan gratuito real **sin tarjeta**, sus límites de uso, sus términos (¿uso no comercial?,
¿atribución?, ¿permite alimentar a un LLM?) y su `robots.txt`. Si no cumple, descártala y déjalo anotado
como las que ya se descartaron (`app/news/sources.py`: Cointelegraph, CoinDesk y Decrypt). No inventes la
forma de la respuesta: contrasta con un ejemplo real de la documentación.

1. **El registro**, en `app/news/sources.py`: una constante con el `id` y una entrada de `SOURCES` con
   `label`, `kind` (`headlines` o `market`), `url`, `min_interval_s` (el intervalo mínimo entre consultas
   completas, que respeta el límite de la fuente), `attribution` (el texto de atribución que debe verse junto a
   sus datos) y, si hace falta, `request_gap_s`.
2. **El parser**, en `app/news/parsers.py`: una función pura de texto o bytes a `ParsedItem`/`FearGreedPoint`,
   que **rechaza** XML con `DOCTYPE` o entidades (`UnsafeXmlError`) y valida cada campo. Solo se guarda el
   título, la URL, el dominio y la fecha: **nunca el cuerpo del artículo**.
3. **El colector**, en `app/news/collector.py`: un método `_collect_<id>` (o reutiliza `_collect_rss`) y su
   rama en `_collect`. Respeta el límite de la fuente (`result.rate_limited` -> `SourceRateLimited`), usa GET
   condicional donde exista (`ETag`, `Last-Modified`) y termina con `_finish_ok`. Cada fuente corre en su
   propio `try/except` con un tope de 600 s y, tras un fallo, un backoff exponencial.
4. **La configuración:** el `id` en `NEWS_SOURCES` (por defecto `gdelt,fed_monetary,fear_greed`) solo si debe
   activarse para todos; el valor por defecto se repite en `.env.example` y en
   [`../CONFIGURACION.md`](../CONFIGURACION.md).
5. **La atribución visible:** `attribution_lines(settings.news_sources_list)` la entrega a la API
   (`GET /api/news/recent` y el bloque `news` de `GET /health`) y `frontend/src/NewsMemoryCard.tsx` la
   muestra; comprueba que la de la fuente nueva aparece.
6. **Las pruebas:** `tests/unit/test_news_collector.py` y `test_news_core.py` con `httpx.MockTransport` y
   respuestas de muestra; un 429, un 304, un cuerpo enorme y un XML hostil. `test_news_runtime_isolation.py`
   vigila que `app.news` no importe el núcleo.
7. **La documentación:** la tabla de fuentes y sus términos en [`../COSTOS_Y_LIMITES.md`](../COSTOS_Y_LIMITES.md);
   [`modulos/news.md`](modulos/news.md).
8. **La salud:** la clave `news:<id>` de `data_source_health` aparece sola en `GET /health` y en
   `scripts/news_status.py`.

## Cambiar un parámetro de riesgo

Los parámetros del motor de riesgo (`app/trading/risk_engine.py`) solo gobiernan la **cuenta paper** (el
brazo ejecutado): las sombras tienen margen ilimitado y no pasan por él. Cambiarlos **no invalida la
medición**, pero sí las reglas con que se evaluarán los criterios de paso a dinero real (los 30 días y las 100
operaciones del brazo): si cambian a mitad, la muestra mezcla dos conjuntos de reglas. Anótalo en
`docs/historial/PROGRESS.md` con la fecha.

| Parámetro | Qué bloquea | Rechazo en `risk_rejections` |
|---|---|---|
| `MAX_SIMULTANEOUS_POSITIONS` (3) | cupo total de posiciones abiertas | `MAX_SIMULTANEOUS_POSITIONS` |
| `MAX_SAME_DIRECTION_POSITIONS` (2) | cupo en la misma dirección | `MAX_SAME_DIRECTION_POSITIONS` |
| `MAX_DRAWDOWN_PCT` (0.20) y `DRAWDOWN_STOP_MODE` (`duro`) | el stop por drawdown (en `alerta` solo avisa y cuenta) | `DRAWDOWN_STOP_ACTIVE` |
| `MAX_DAILY_LOSS_PCT` (0.05) | la pérdida diaria, que se engancha hasta el cambio de día local | `DAILY_LOSS_LIMIT` |
| `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER` (4) y `CIRCUIT_BREAKER_COOLDOWN_HOURS` (8) | el circuit breaker | `CIRCUIT_BREAKER_ACTIVE` |
| `MAX_CAPITAL_PCT_PER_ASSET` (0.10) | el tope de margen por símbolo (se ajusta, no rechaza) | `MARGIN_UNAVAILABLE` si queda en 0 |
| `WS_STALE_AFTER_SECONDS` (45) | el feed de precios obsoleto | `DATA_STALE` |
| `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` | la elegibilidad por estrategia | `STRATEGY_NOT_ELIGIBLE` (**protegida**) |
| `LIVE_SL_MARGIN_CAP_PCT` (50.0) | el tope del SL sobre el margen | `SL_CAP_EXCEEDED` (**protegida**) |

1. **Cambia el valor por defecto en `app/config.py`**, en `.env.example` y en
   [`../CONFIGURACION.md`](../CONFIGURACION.md) (las tres deben coincidir; hay pruebas). O solo pon el valor en
   tu `.env` si es un cambio de tu instalación y no del proyecto.
2. **Mantén la escala:** `MAX_DRAWDOWN_PCT`, `MAX_DAILY_LOSS_PCT` y `MAX_CAPITAL_PCT_PER_ASSET` son
   **fracciones** (`0.20` = 20 %); `LIVE_SL_MARGIN_CAP_PCT` es un **porcentaje** (`50.0` = 50 %). El bot se
   niega a arrancar con un valor fuera de escala.
3. **Las pruebas:** `tests/unit/test_risk_engine.py` y `test_config_risk_sizing.py`, con el valor exactamente
   **en** el umbral y a ambos lados.
4. **El estado persistido manda:** el pico de equity, la línea base del día, la racha de pérdidas y los
   bloqueos viven en `system_state` y sobreviven a un reinicio. Cambiar un parámetro no recalcula lo ya
   guardado.
5. **Un código de rechazo nuevo** (una regla nueva, no un parámetro): agrega el motivo a `check_new_entry`,
   a `REASON_LEGEND` de `app/api/routes/arm.py` (`RISK_<código>`) y a su prueba.
6. **`LIVE_SL_MARGIN_CAP_PCT` y `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` no se cambian durante la medición** aunque
   aparezcan aquí: además de la cuenta paper, deciden qué señales entran a la medición
   ([Tope de SL](ZONAS_PROTEGIDAS.md#tope-de-sl), [Estrategias y parámetros](ZONAS_PROTEGIDAS.md#estrategias-y-parámetros)).

## Agregar un script de operación

1. **El archivo** en `scripts/`, con un docstring de módulo que explique el uso, las opciones y los códigos de
   salida. Si lee la base, acepta `--db` y comprueba con un `assert` que la ruta **no** es
   `settings.database_path`; ábrela con `mode=ro` si solo lee (copia `_connect_ro` de
   `scripts/measurement_status.py`).
2. **Ensáyalo contra una copia temporal de la base real** antes de entregarlo (regla del proyecto: las pruebas
   con datos sintéticos no bastan). Con Docker: `python scripts/docker_db.py run <script>.py` saca una copia
   fresca, la pasa con `--db` y la borra.
3. **Con la medición ciega**, imprime solo conteos, nunca PnL, tasas ni lecciones activas.
4. **Procesos hijos:** `creationflags=NO_WINDOW` (de `scripts/docker_support.py`).
5. **Lanzado desde otra carpeta** (Programador de tareas, `docker exec`): fija la raíz del repositorio antes
   de importar `app` (mira `measurement_identity.py`).
6. **La prueba** en `tests/unit/test_<script>_script.py` y la fila del script en la tabla de
   [`../USO.md`](../USO.md#referencia-de-scripts) (`test_every_script_is_described_in_the_usage_guide`).
7. **Si va en la imagen**, comprueba `.dockerignore`: salvo los que lista, todo `scripts/` viaja en la imagen.

## Agregar una pieza opcional aislada

Es el patrón de Telegram, el push, las noticias, la memoria y el brazo ejecutado. Una pieza opcional **nunca**
puede frenar al scheduler, al etiquetado ni al monitor.

1. **Su interruptor propio**, apagado por defecto ([receta de la variable](#agregar-una-variable-de-configuración)).
2. **Un módulo `runtime.py`** en su paquete con `build_<pieza>(settings, ...) -> Pieza | None` (no lanza: si
   algo falla registra solo el tipo de la excepción y devuelve `None`), `run_isolated(pieza)` (captura
   cualquier `Exception` que escape de `run_forever`, salvo `CancelledError`) y `maybe_start_<pieza>(settings, db,
   tasks)` (crea la tarea y la agrega a `tasks`, para que el cierre del `lifespan` la cancele con las demás).
   Mira `app/news/runtime.py`.
3. **Su paquete no importa el núcleo de la medición** ni la API de Anthropic.
4. **Lo enciende el lanzador del bot real** y no el servidor de desarrollo: `docker-compose.yml`,
   `scripts/start_bot.ps1` y el candado de `scripts/run_dev_server.py`.
5. **La prueba de aislamiento:** apagada por defecto; construirla no lanza; un fallo y un cuelgue suyos no
   frenan al scheduler, al etiquetado ni al monitor; el paquete no importa lo prohibido. Copia
   `tests/unit/test_news_runtime_isolation.py`.
6. **Arráncala en el `lifespan`** (`app/main.py`), cuelga lo que las rutas necesiten de `app.state`, y
   considera un bloque en `GET /health` con solo conteos y el tipo del último error.

## Desplegar un cambio al contenedor

> **Este procedimiento lo ejecuta una persona.** El bot real corre en el contenedor `minerva` (puerto 8000)
> y una sesión de desarrollo (incluida una asistida por IA) no lo detiene, reinicia ni reconstruye. El
> contenedor sigue con el código viejo hasta que se reconstruye la imagen y se recrea.

**Qué necesita un despliegue y qué no:**

| Cambio | Qué hace falta |
|---|---|
| Solo documentación (`docs/`) | **nada**: `./docs` está montada de solo lectura y el bot la lee en cada petición |
| El `.env` o `docker-compose.yml` | `docker compose up -d --force-recreate` (un `restart` no garantiza ver un `.env` nuevo) |
| Código de `app/` o `scripts/`, o el frontend | reconstruir la imagen y recrear el contenedor (`docker compose up -d --build`) |
| Dependencias de Python | regenerar `docker/requirements.lock` (`python scripts/export_docker_requirements.py`) y reconstruir |

### Antes (en tu equipo de desarrollo)

1. **Árbol limpio y confirmado.** La imagen se construye con el **árbol de trabajo** (`COPY app/ ./app/`), no
   con lo que haya en git: lo que no hayas confirmado igual entraría a la imagen. `git status` debe estar
   limpio.
2. **El prompt congelado no cambió:**

   ```bash
   git diff --stat HEAD origin/master -- app/llm/prompts.py app/llm/market_features.py app/trading/shadow_live_labeler.py
   python scripts/check_measurement_safety.py --since-measurement
   ```

   El primero no debe imprimir nada. El segundo lista lo protegido que cambió desde el congelamiento; lo ya
   explicado está en [la tabla de cambios](ZONAS_PROTEGIDAS.md#cambios-ya-hechos-en-zonas-protegidas-desde-el-congelamiento),
   y cualquier cosa nueva debe tener una razón escrita.
3. **Todo en verde:** `python -m ruff check .`, `python -m pytest`, `npm --prefix frontend run test` y
   `npm --prefix frontend run build`; `python scripts/build_dev_docs.py --check`.
4. **Si cambia la base,** ensáyala contra una copia (ver [`MODELO_DATOS.md`](MODELO_DATOS.md#cómo-agregar-una-columna-sin-romper-una-base-existente)).
5. **Elige la hora** (abajo) y haz un **respaldo justo antes:** `python scripts/docker_db.py backup`.

### La ventana segura

Entre los minutos **:10 y :40** de una hora UTC que **no** sea de cierre de vela de 4 h (**00, 04, 08, 12,
16 y 20**). Todas las filas de la medición caen en el minuto :00 o :01; a partir de :10 ya no queda un
etiquetado en vuelo que el apagado pueda cortar, y la siguiente tanda llega al menos 20 minutos después.
Comprobación rápida:

```bash
python -c "from datetime import datetime, UTC; n = datetime.now(UTC); print(n.strftime('%H:%M UTC'), 'ventana segura' if n.hour % 4 and 10 <= n.minute <= 40 else 'FUERA de la ventana')"
```

El detalle (horas válidas en tu zona horaria) está en [`../USO.md`](../USO.md#reinicios-seguros).

### Desplegar

```bash
git pull                       # solo si despliegas desde otro clon; si es el mismo árbol, sáltalo
docker compose up -d --build   # construye minerva:local y recrea el contenedor
```

- La imagen se construye mientras el bot viejo sigue corriendo; la parada (SIGTERM, hasta 30 s para cerrar
  ordenadamente) ocurre cuando la imagen nueva está lista. Lanza el comando con margen para que el
  **reinicio** caiga dentro de la ventana. *Este orden es el comportamiento habitual de Compose; confírmalo en
  el primer despliegue observando `docker compose ps`.*
- Compose recrea el contenedor solo si cambió la imagen o la configuración; los **volúmenes se conservan**
  (la base está en `minerva_data`).
- **Nunca** `docker compose down -v` ni `docker volume rm`: borran la medición
  ([qué borra el volumen](../USO.md#qué-borra-el-volumen-de-docker)).

### Verificar (en 2 minutos)

1. `docker compose ps`: el estado pasa a `healthy` (hasta unos 5 minutos el primer arranque).
2. `GET /health` (por ejemplo `python scripts/check_health.py --port 8000`):
   - `status: ok`;
   - `llm_labeling.frozen_hash_ok: true` y `llm_labeling.enabled: true`;
   - `paper_execution.active: true`;
   - `control.paused: false`.
3. **No abras los logs** si quieres mantener la medición ciega ([límite conocido](ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados)).
4. **Pasada la siguiente hora en punto,** una copia nueva y `python scripts/docker_db.py run
   measurement_status.py`: suben las filas de la medición y "Grupos omitidos" no crece. Y
   `python scripts/docker_db.py run measurement_identity.py`: una sola identidad (modelo, prompt y versión de
   features).

### Si algo sale mal

- **`frozen_hash_ok: false`:** el prompt o las features cambiaron respecto del congelado: el etiquetado se
  detiene solo. Revierte el cambio de `app/llm/` y vuelve a desplegar.
- **No arranca o no llega a `healthy`:** `docker compose logs --tail 100 minerva` (solo lectura) para el error
  de arranque. Un `.env` ausente o una carpeta con su nombre es la causa más probable
  ([`../SOLUCION_PROBLEMAS.md`](../SOLUCION_PROBLEMAS.md)).
- **Volver atrás:** revierte el commit en git y repite el despliegue, otra vez en la ventana segura. Las
  columnas añadidas a la base se quedan y el código viejo las ignora
  ([`MODELO_DATOS.md`](MODELO_DATOS.md#migraciones)). Si hace falta restaurar la base, hay que hacerlo con el
  bot detenido ([`../USO.md`](../USO.md#restaurar)).
- **Apaga lo nuevo sin revertir:** un interruptor se apaga en `docker-compose.yml` y se recrea el
  contenedor. **No uses `/kill` ni `/pause` como freno:** detienen la medición.

## Lo no verificado

- El orden "construir la imagen y luego parar el contenedor viejo" de `docker compose up -d --build` es el
  comportamiento habitual de Compose; no se observó en este proyecto mientras se escribía.
- Los tiempos (unos 1 o 2 minutos de construcción, hasta 5 minutos para `healthy`) son los que cita
  [`../USO.md`](../USO.md#arrancar-y-detener); dependen del equipo.
