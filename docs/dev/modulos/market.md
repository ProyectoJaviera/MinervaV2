# Guía del paquete `market`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/market.html`](../api/app/market.html)

## Qué hace el paquete

Los datos de mercado: los clientes REST y WebSocket **públicos** de Bitunix, el cliente de CoinGecko, la descarga
incremental de velas y de *funding*, las especificaciones de contrato y la construcción del **universo** de activos
(CoinGecko ∩ perpetuos USDT de Bitunix). Solo usa endpoints públicos y gratuitos (v1 no usa credenciales de Bitunix) y
**nada de este paquete escribe en la cuenta paper**: lo que persiste son cachés (velas, *funding*, contratos) y el universo.

## Zona protegida

**Parcial.** El **universo** (qué símbolos se miden) es una zona protegida; también el significado de las velas cerradas y del
*funding* que entran a las señales. Los clientes de red, el limitador de tasa y la atribución se pueden mejorar con
cuidado. Ver [Universo](../ZONAS_PROTEGIDAS.md#universo).

## Cómo se conecta

- Todos los clientes HTTP se construyen con `app/core/http.py::build_async_http_client()`.
- El `BitunixRestClient` es **uno solo** (creado en `app/main.py`) y lo comparten el generador de señales, el monitor, la
  cuenta paper, el brazo ejecutado y el refresco del universo: su limitador de tasa (10 pedidos por segundo por IP) es común.
- `universe.py` alimenta a `signal_generator` (qué símbolos evaluar), al WebSocket (qué símbolos suscribir) y a la API y
  Telegram (`universe_diff.py`).
- Las velas y el funding se guardan en `ohlcv_cache`, `ohlcv_floor`, `ohlcv_series_complete`, `ohlcv_unrepairable_gaps`,
  `funding_cache` y `contract_specs_cache`; el universo, en `asset_universe`.

## Archivos

### `app/market/__init__.py`

**Responsabilidad.** Docstring del paquete: solo endpoints públicos y gratuitos; ninguna pieza escribe en la cuenta paper.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/market/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market.html`](../api/app/market.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/market/__init__.py -->

### `app/market/attribution.py`

**Responsabilidad.** La atribución obligatoria de CoinGecko: `COINGECKO_TEXT` («Data provided by CoinGecko»), `COINGECKO_URL`
y el diccionario `COINGECKO_ATTRIBUTION`.

**Funciones y clases clave.** Solo constantes.

**Invariantes — no romper.** El texto debe mostrarse en un lugar visible y cercano a donde se muestran los datos de
CoinGecko (frontend y avisos), con enlace a `https://www.coingecko.com`; los términos de la API piden además letra legible
de al menos 10 px. Verificado el 2026-10-18 en la guía oficial de atribución.

**Trampas conocidas.** Es un requisito de los términos del plan gratuito, no un adorno.

<!-- hechos:app/market/attribution.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/attribution.html`](../api/app/market/attribution.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.api.routes.universe`, `app.telegram.push`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_push.py`, `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/market/attribution.py -->

### `app/market/bitunix_rest.py`

**Responsabilidad.** El cliente REST **público** de Bitunix Futures (base `https://fapi.bitunix.com`), con limitador de tasa
y reintentos con backoff.

**Funciones y clases clave.**
- `BitunixRestClient.get_kline()` (máximo verificado: 200 velas por llamada), `get_tickers()`, `get_trading_pairs()`,
  `get_depth()`, `get_funding_rate_batch()`, `get_funding_rate_history()` (máximo 200 por llamada) y `get_position_tiers()`.
- `_get()` — GET con límite de tasa, reintentos para `RETRYABLE_STATUS` (429, 500, 502, 503, 504) y validación del `code` del
  payload: una respuesta 200 con un código de error lanza `BitunixApiError`.

**Invariantes — no romper.**
- **Nunca envía `api-key`, `sign` ni ningún encabezado de autenticación.** Regla crítica de CLAUDE.md: v1 es 100 % paper.
- No se inventan endpoints ni parámetros: cada uno se verificó contra la documentación oficial (`docs/historial/FASE0.md`).
  Lo que no se pudo verificar está en [COSTOS_Y_LIMITES](../../COSTOS_Y_LIMITES.md#lo-no-verificado).

**Trampas conocidas.** Una respuesta HTTP 200 **no** significa éxito: hay que mirar el `code` del payload.

<!-- hechos:app/market/bitunix_rest.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/bitunix_rest.html`](../api/app/market/bitunix_rest.html)
- **Importa (internos):** `app.core.http`, `app.core.logging`, `app.market.rate_limiter`
- **Lo importan:** `app.core.scheduler`, `app.execution.paper_backend`, `app.main`, `app.market.contract_specs`, `app.market.funding_history`, `app.market.ohlcv_history`, `app.market.universe`, `app.trading.position_monitor`, `app.trading.signal_generator`, `scripts.download_history`, `scripts.repair_ohlcv_gaps`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/integration/test_bitunix_live.py`, `tests/unit/test_bitunix_rest.py`
<!-- /hechos:app/market/bitunix_rest.py -->

### `app/market/bitunix_ws.py`

**Responsabilidad.** El cliente WebSocket público de Bitunix, canal `tickers`, con reconexión automática.

**Funciones y clases clave.**
- `BitunixPublicWSClient.run_forever()` — mantiene la conexión hasta `stop()`, reconectando con backoff exponencial
  (hasta `MAX_BACKOFF_SECONDS`, 60 s) y enviando `ping` cada `PING_INTERVAL_SECONDS` (15 s).
- `set_symbols()` — cambia el conjunto suscrito (lo llama `Scheduler.sync_ws_subscriptions`).
- Entrega **todo** mensaje JSON del servidor (el `pong` incluido) a `on_message` (`PositionMonitor.handle_ws_message`).

**Invariantes — no romper.**
- Formato verificado: suscripción `{"op": "subscribe", "args": [{"symbol": ..., "ch": "tickers"}]}`; el push trae el último
  precio en `la` pero **no** el mark price (ese se consulta por REST).
- Todo mensaje cuenta como latido del feed: con más de `WS_STALE_AFTER_SECONDS` (45 s, 3× el ping) sin latido el motor de
  riesgo no abre entradas nuevas (`DATA_STALE`), pero **nunca** bloquea cierres.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/market/bitunix_ws.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/bitunix_ws.html`](../api/app/market/bitunix_ws.html)
- **Importa (internos):** `app.core.logging`
- **Lo importan:** `app.core.scheduler`, `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/market/bitunix_ws.py -->

### `app/market/coingecko_client.py`

**Responsabilidad.** El cliente de CoinGecko (plan Demo gratuito) con reintentos y backoff.

**Funciones y clases clave.**
- `CoinGeckoClient.get_markets()` — ranking por capitalización (`/coins/markets` de CoinGecko, `market_cap_desc`), con filtro opcional
  por `category`; `get_categories_list()`; `CoinGeckoApiError`.

**Invariantes — no romper.**
- El encabezado `x-cg-demo-api-key` se envía **solo** si `COINGECKO_API_KEY` está configurada (puede quedar vacía).
- El parámetro `category` solo **filtra por inclusión**: no existe forma de excluir una categoría en la misma llamada; por eso
  el universo pide cada categoría a excluir y resta.
- Respetar los límites del plan gratuito y la atribución (ver `attribution.py`).

**Trampas conocidas.** El endpoint `/coins/categories/list` de CoinGecko respondió 200 sin API key en las pruebas, pero el plan Demo formal exige una
key gratuita: no depender de ese comportamiento.

<!-- hechos:app/market/coingecko_client.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/coingecko_client.html`](../api/app/market/coingecko_client.html)
- **Importa (internos):** `app.core.http`, `app.core.logging`
- **Lo importan:** `app.main`, `app.market.universe`, `scripts.download_history`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_universe_refresh.py`
<!-- /hechos:app/market/coingecko_client.py -->

### `app/market/contract_specs.py`

**Responsabilidad.** Obtener y cachear las especificaciones de contrato por símbolo.

**Funciones y clases clave.**
- `refresh_spec()` — consulta `trading_pairs` (precisión, mínimos, apalancamiento), `position_tiers` (margen de mantenimiento
  por tramo, para la liquidación) y `funding_rate/batch` (intervalo de funding) y lo guarda en `contract_specs_cache`.

**Invariantes — no romper.** Campos verificados en `docs/historial/FASE0.md`: `minTradeVolume`, `basePrecision`,
`quotePrecision`, `minLeverage`, `maxLeverage`, `symbolStatus`, `fundingInterval`, etc.

**Trampas conocidas.** `minTradeVolume` decide si una orden de margen pequeño es ejecutable (`BELOW_MIN_ORDER` del brazo).

<!-- hechos:app/market/contract_specs.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/contract_specs.html`](../api/app/market/contract_specs.html)
- **Importa (internos):** `app.core.logging`, `app.market.bitunix_rest`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.specs_repo`
- **Lo importan:** `app.trading.signal_generator`, `scripts.download_history`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** `contract_specs_cache`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/market/contract_specs.py -->

### `app/market/funding_history.py`

**Responsabilidad.** La descarga incremental y reanudable de `funding_rate_history`, con caché en `funding_cache`.

**Funciones y clases clave.**
- `download_missing_funding()` — descarga por red **solo** la cola o la cabeza del historial que falte; la usan el generador de
  señales (cada ciclo) y `scripts/download_history.py`.

**Invariantes — no romper.**
- A diferencia de las velas, el funding no es indispensable para el backtest: sin eventos reales, `backtesting/funding.py` aproxima
  con la mediana y marca `funding_is_approximated`. Esa marca es la que impide que una estrategia de percentiles opere con dato
  inventado.

**Trampas conocidas.** El historial de funding de Bitunix llega solo hasta ~2024.

<!-- hechos:app/market/funding_history.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/funding_history.html`](../api/app/market/funding_history.html)
- **Importa (internos):** `app.core.logging`, `app.market.bitunix_rest`, `app.market.ohlcv_history`, `app.persistence.database`, `app.persistence.repositories.funding_repo`, `app.persistence.repositories.ohlcv_repo`
- **Lo importan:** `app.trading.position_monitor`, `app.trading.signal_generator`, `scripts.download_history`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `funding_cache`, `ohlcv_floor`
- **Tablas que escribe:** `funding_cache`, `ohlcv_floor`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_funding_history.py`
<!-- /hechos:app/market/funding_history.py -->

### `app/market/ohlcv_history.py`

**Responsabilidad.** La descarga incremental y reanudable de velas, con caché en `ohlcv_cache`: descarga **solo** lo que falta,
nunca el rango completo de nuevo.

**Funciones y clases clave.**
- `download_missing()` — descarga la cola reciente y la cabeza vieja que falten, y **verifica y repara huecos** al final. Cada
  página se guarda al instante: interrumpirla no pierde progreso. La usan `scripts/download_history.py` y la reconciliación al
  reiniciar (velas de 1 minuto).
- `get_cached_or_raise()` — lectura **pura** de la caché, sin red; si el rango no está completo lanza
  `MissingHistoricalDataError`. La usa el motor de backtest, que ya no toca la red.
- `find_gaps()` / `fill_gaps()` — detectan y reparan huecos puntuales con pedidos **estrechos**.
- `check_series_availability()`, `drop_incomplete_last_bar()` (descarta la vela en formación) e `interval_to_ms()`.

**Invariantes — no romper.**
- Máximo 200 velas por llamada, paginando hacia **atrás** desde `end_time`.
- `endTime` es **estrictamente exclusivo sobre `open_time`** (`open_time < endTime`); verificado empíricamente el 2026-10-10. Por
  eso `cursor = oldest_time − 1` ya es correcto.
- Una paginación correcta **no alcanza**: Bitunix a veces omite una vela real en una respuesta ancha (`limit=200` sin
  `startTime`) que sí aparece con una ventana chica. No se encontró una regla determinista, ni hay documentación oficial. Por eso
  `download_missing` verifica con `find_gaps` y repara con `fill_gaps`.

**Trampas conocidas.** La versión anterior de `get_or_fetch` mezclaba red y caché y volvía a pedir el **rango completo** si no
estaba 100 % cubierto: cientos de solicitudes desperdiciadas por reintento. Un hueco confirmado como irreparable
(`ohlcv_unrepairable_gaps`) no se vuelve a pedir durante `GAP_RETRY_COOLDOWN_HOURS` (24 h).

**Protección.** Parcial: lo que decide qué vela cuenta como cerrada alimenta las señales.

<!-- hechos:app/market/ohlcv_history.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/ohlcv_history.html`](../api/app/market/ohlcv_history.html)
- **Importa (internos):** `app.core.logging`, `app.market.bitunix_rest`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.ohlcv_repo`
- **Lo importan:** `app.backtesting.engine`, `app.backtesting.report`, `app.llm.market_features`, `app.market.funding_history`, `app.trading.position_monitor`, `app.trading.signal_generator`, `scripts.download_history`, `scripts.repair_ohlcv_gaps`, `scripts.run_backtest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `ohlcv_cache`, `ohlcv_floor`, `ohlcv_series_complete`, `ohlcv_unrepairable_gaps`
- **Tablas que escribe:** `ohlcv_cache`, `ohlcv_floor`, `ohlcv_unrepairable_gaps`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backtest_engine.py`, `tests/unit/test_backtest_sanity.py`, `tests/unit/test_download_history.py`, `tests/unit/test_funding_contrarian_percentile_strategy.py`, `tests/unit/test_ohlcv_history.py`, `tests/unit/test_run_backtest_script.py`, `tests/unit/test_run_full_backtest_smoke.py`, `tests/unit/test_signal_generator.py`
<!-- /hechos:app/market/ohlcv_history.py -->

### `app/market/rate_limiter.py`

**Responsabilidad.** El limitador de tasa de **ventana deslizante** de un segundo para respetar el límite de Bitunix.

**Funciones y clases clave.**
- `RateLimiter.acquire()` — espera, si hace falta, hasta poder hacer la llamada y la registra.

**Invariantes — no romper.** Es de ventana deslizante, no un *token bucket* estricto; el límite verificado es de 10 pedidos por
segundo por IP para todos los endpoints públicos usados.

**Trampas conocidas.** El limitador es por cliente: todos deben compartir la misma instancia de `BitunixRestClient`.

<!-- hechos:app/market/rate_limiter.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/rate_limiter.html`](../api/app/market/rate_limiter.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.market.bitunix_rest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/market/rate_limiter.py -->

### `app/market/universe.py`

**Responsabilidad.** Construye y refresca el **universo dinámico**: el top-N de CoinGecko por capitalización, menos
stablecoins / *wrapped* / *liquid staking*, intersectado con los perpetuos USDT con `symbolStatus = OPEN` de Bitunix, con
lista manual de exclusión y una comprobación de que el precio de CoinGecko y el de Bitunix no difieren más de la tolerancia.

**Funciones y clases clave.**
- `build_universe_snapshot()` — toma los `UNIVERSE_CANDIDATE_POOL` (30) primeros de CoinGecko, marca categorías excluidas,
  exclusiones manuales, perpetuo disponible y sanidad de precio, e incluye hasta `UNIVERSE_SIZE` (10). Devuelve **todos** los
  candidatos evaluados (no solo los incluidos) para que la exclusión quede auditable.
- `refresh_universe()` — guarda el snapshot en `asset_universe`; no guarda uno sin ningún símbolo incluido.
- `refresh_universe_safely()` — nunca lanza: ante un fallo conserva el último snapshot y deja el error en
  `system_state.universe_last_refresh_error`.
- `is_universe_stale()` — más de `UNIVERSE_STALENESS_HOURS` (48 h) o sin snapshot.
- `UniverseRefresher` — tarea que cada hora comprueba si pasaron `UNIVERSE_REFRESH_HOURS` (24 h).

**Invariantes — no romper.**
- **Degradación controlada:** si el refresco falla se conserva el último snapshot; con el universo obsoleto el generador de
  señales **no genera nada** (los cierres no se ven afectados).
- Es una tarea aparte: un fallo suyo nunca detiene el monitor ni los cierres.

**Trampas conocidas.** El universo **cambia con el ranking**: el conjunto de símbolos medidos no es fijo, y cada refresco deja un
snapshot nuevo (`asset_universe` conserva el historial).

**Protección.** Sí: [Universo](../ZONAS_PROTEGIDAS.md#universo).

<!-- hechos:app/market/universe.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/universe.html`](../api/app/market/universe.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.market.bitunix_rest`, `app.market.coingecko_client`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.system_state_repo`, `app.persistence.repositories.universe_repo`
- **Lo importan:** `app.api.routes.universe`, `app.main`, `app.trading.signal_generator`, `scripts.download_history`
- **Configuración que lee:** `UNIVERSE_CANDIDATE_POOL`, `UNIVERSE_PRICE_SANITY_TOLERANCE_PCT`, `UNIVERSE_REFRESH_HOURS`, `UNIVERSE_SIZE`, `UNIVERSE_STALENESS_HOURS`, `universe_exclude_categories_list`, `universe_manual_exclusions_list`
- **Tablas que lee:** `asset_universe`
- **Tablas que escribe:** `asset_universe`, `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_universe_refresh.py`
<!-- /hechos:app/market/universe.py -->

### `app/market/universe_diff.py`

**Responsabilidad.** Los cambios del universo entre refrescos: una función pura que comparten la API del dashboard y las
notificaciones de Telegram.

**Funciones y clases clave.**
- `diff_sets()` y `universe_changes()`; `UniverseChange`.

**Invariantes.** El universo vigente son los activos `included` del último snapshot; un cambio es que el conjunto de incluidos
difiera del anterior.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/market/universe_diff.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/market/universe_diff.html`](../api/app/market/universe_diff.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.api.routes.universe`, `app.telegram.push`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/market/universe_diff.py -->
