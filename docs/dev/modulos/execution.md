# Guía del paquete `execution`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/execution.html`](../api/app/execution.html)

## Qué hace el paquete

Es la **cuenta paper**: abre y cierra posiciones simuladas con el precio real de Bitunix, aplica comisiones y registra todo en la
tabla `trades`. Contiene también las fórmulas de comisiones y PnL, que **comparten** la cuenta paper en vivo, las sombras
(la medición) y el backtest, para que una misma estrategia se comporte igual en los tres. La ejecución real
(`BitunixBackend`) existe solo como interfaz documentada y desactivada: **v1 es 100 % paper trading**.

## Zona protegida

`pnl.py` es protegida: sus fórmulas dan el resultado de las sombras, que es lo que mide la medición. Ver
[ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#sombras). `paper_backend.py` pertenece a la cuenta paper (no a la medición) y se
puede tocar con las precauciones del motor de riesgo (ver [trading](trading.md#apptradingrisk_enginepy)).

## Cómo se conecta

- `app/main.py` crea un único `PaperBackend` y se lo pasa al monitor, al scheduler, al brazo ejecutado y a los comandos de
  Telegram; también cuelga de `app.state` para que la ruta de la cuenta del dashboard (`app/api/routes/account.py`) calcule
  el PnL flotante.
- `PaperBackend.open_position` **siempre** pasa por `risk_engine.check_new_entry`; los cierres (`close_if_open`) nunca.
- El `PositionMonitor` cierra con `close_if_open` a un precio explícito (SL, TP, trailing, liquidación).
- `app/execution/pnl.py` lo usan también `app/trading/fills.py` (cierres con slippage y funding) y `app/backtesting/engine.py`.

## Archivos

### `app/execution/__init__.py`

**Responsabilidad.** Docstring del paquete: dice que `BitunixBackend` es solo interfaz y que una prueba por AST comprueba
que nada de `app/` lo importa.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/execution/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/execution.html`](../api/app/execution.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/execution/__init__.py -->

### `app/execution/backend_base.py`

**Responsabilidad.** La interfaz `ExecutionBackend` (clase abstracta) con las capacidades 1, 2, 6 y 7 de la
especificación: abrir una posición, cerrarla, saldo y posiciones abiertas.

**Funciones y clases clave.**
- `ExecutionBackend` — `open_position`, `close_position`, `get_balance` y `get_positions`.

**Invariantes.**
- Las capacidades de TP, SL y trailing al 100 % **no** están en la interfaz a propósito: viven en `PaperBackend` y en el
  monitor, para no comprometer un diseño que no se validó con datos reales.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/execution/backend_base.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/execution/backend_base.html`](../api/app/execution/backend_base.html)
- **Importa (internos):** `app.persistence.models`
- **Lo importan:** `app.execution.bitunix_backend`, `app.execution.paper_backend`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/execution/backend_base.py -->

### `app/execution/bitunix_backend.py`

**Responsabilidad.** Dejar escrito el diseño de la futura ejecución real y lo que **falta verificar** antes de
implementarla. **No se puede instanciar**: `__init__` y los cuatro métodos lanzan `NotImplementedError`.

**Funciones y clases clave.**
- `BitunixBackend` — misma interfaz que `PaperBackend`, todo desactivado.

**Invariantes — no romper.**
- Nada de `app/` puede importar este módulo; lo comprueba `tests/unit/test_paper_execution_isolation.py` por AST
  (ningún módulo de `app/` lo importa, `app/main.py` no lo nombra y el brazo ejecutado solo conoce `PaperBackend`).
- Regla de CLAUDE.md: v1 nunca envía órdenes reales. La activación futura exigiría una confirmación explícita fuera de
  este código (no un flag de entorno) y verificar cada endpoint contra la documentación oficial.

**Trampas conocidas.**
- Lo **verificado** es solo la autenticación REST (cabeceras `api-key`, `nonce`, `timestamp`, `sign` HMAC-SHA256). Lo
  **no verificado**: ruta y carga de las órdenes a mercado, fijar apalancamiento y margen aislado, saldo y posiciones
  autenticados, y los límites de tasa de los endpoints autenticados.

<!-- hechos:app/execution/bitunix_backend.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/execution/bitunix_backend.html`](../api/app/execution/bitunix_backend.html)
- **Importa (internos):** `app.execution.backend_base`, `app.persistence.models`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/execution/bitunix_backend.py -->

### `app/execution/paper_backend.py`

**Responsabilidad.** La única implementación activa de `ExecutionBackend`: la cuenta paper. Abre con el precio real de
mercado (`markPrice` del ticker de Bitunix, o `lastPrice` si falta), aplica la comisión taker configurada y escribe en la
base local. Nunca envía una orden real.

**Funciones y clases clave.**
- `PaperBackend.open_position()` — bajo el candado de la cuenta: calcula el equity → pide permiso a
  `risk_engine.check_new_entry` (que registra el rechazo y puede **reducir** el margen) → comprueba el volumen mínimo
  del contrato → resuelve SL, TP, trailing y liquidación al precio de llenado (`app/trading/levels.py`) → inserta el
  `Trade`. Sin `decision_source` queda `MANUAL` (si `is_manual`) o `SIN_LLM`; el brazo ejecutado pasa `LLM_APROBADA`.
- `PaperBackend.close_if_open()` — cierra a un precio explícito (lo usa el monitor): acumula el funding pendiente
  hasta el instante del cierre, liquida comisiones y slippage, guarda el cierre y **después** actualiza la racha de
  pérdidas, el circuit breaker y el pico de equity. Devuelve `None` si ya estaba cerrada.
- `PaperBackend.close_position()` — cierre manual al `markPrice` actual (fuente `REST_MARK`).
- `PaperBackend.get_equity()` / `get_balance()` / `unrealized_pnl_usdt()` — saldo realizado, equity (más PnL flotante
  neto de funding y slippage) y PnL flotante de una posición.
- `InsufficientRiskBudgetError` — la cantidad queda bajo `min_trade_volume` del contrato; el brazo ejecutado la registra
  como `BELOW_MIN_ORDER`.

**Invariantes — no romper.**
- **Un candado** (`_state_lock`) serializa aperturas **y** cierres: el conteo de posiciones del motor de riesgo no puede
  cambiar entre la aprobación y el `INSERT`, y dos cierres concurrentes (monitor y reconciliación) no pueden liquidar la
  misma posición dos veces.
- Los cierres **nunca** pasan por el motor de riesgo (un cierre no se bloquea) y las actualizaciones de racha y equity
  van **después** del cierre, sin poder bloquearlo.
- Solo lee datos públicos de Bitunix y escribe en la base local.

**Trampas conocidas.**
- `get_balance()` lee hasta las 100.000 operaciones más recientes: el costo crece con la historia.
- El precio de llenado es el `markPrice` del instante de apertura; el brazo ejecutado mide la diferencia con el precio
  de la sombra en `paper_execution_log.details_json`.
- Con margen 5 USDT y 10x, un símbolo de precio muy bajo puede quedar bajo el mínimo del contrato y no abrirse (con la
  copia del 2026-10-17, TRXUSDT daba ≈148,9 unidades frente a un mínimo de 150: cifra de esa fecha, no un dato fijo).

<!-- hechos:app/execution/paper_backend.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/execution/paper_backend.html`](../api/app/execution/paper_backend.html)
- **Importa (internos):** 14 módulos (config 1, core 1, execution 2, market 1, persistence 4, trading 5)
- **Lo importan:** `app.core.scheduler`, `app.main`, `app.trading.paper_execution`, `app.trading.position_monitor`
- **Configuración que lee:** `BACKTEST_SLIPPAGE_BPS`, `INITIAL_CAPITAL_USDT`, `TAKER_FEE_PCT`
- **Tablas que lee:** `contract_specs_cache`, `trades`
- **Tablas que escribe:** `trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 18 archivos (e2e 1, unit 17)
<!-- /hechos:app/execution/paper_backend.py -->

### `app/execution/pnl.py`

**Responsabilidad.** Las fórmulas de comisiones y PnL, **compartidas** por la cuenta paper, las sombras y el backtest.

**Funciones y clases clave.**
- `compute_open_fill()` — `nocional = margen × leverage`, `qty = nocional / precio`, `comisión = nocional × taker_fee_pct`.
- `compute_close_result()` — comisión de salida, PnL bruto (según el lado) y neto
  (`bruto − comisión de entrada − comisión de salida − costos extra`).
- `OpenFill`, `CloseResult` — los resultados, inmutables.

**Invariantes — no romper.**
- Son funciones puras, sin red ni base. Cambiar una fórmula cambia a la vez la cuenta paper, el resultado de las sombras y
  los números del backtest ya documentados.
- Los costos extra (slippage, funding) llegan ya calculados: este módulo no sabe cómo se calculan.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/execution/pnl.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/execution/pnl.html`](../api/app/execution/pnl.html)
- **Importa (internos):** `app.persistence.models`
- **Lo importan:** `app.backtesting.engine`, `app.execution.paper_backend`, `app.trading.fills`, `app.trading.paper_execution`, `app.trading.shadow_book`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_pnl.py`
<!-- /hechos:app/execution/pnl.py -->
