# Guía del paquete `backtesting`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/backtesting.html`](../api/app/backtesting.html)

## Qué hace el paquete

El **backtest histórico de la Fase 2**: motor, métricas, veredicto por estrategia y análisis de riesgo de ruina. Es investigación
**offline**: lee velas y *funding* de la caché local, no usa la red, no escribe en la cuenta paper ni en las sombras. Sus
resultados (`backtest_*`) no alimentan la medición del LLM; solo justifican con qué estrategias se parte. Se corre con
`scripts/run_backtest.py` (que **borra** los resultados de corridas anteriores antes de escribir los nuevos).

## Zona protegida

**Un archivo sí: `liquidation.py`**, porque `app/trading/levels.py` lo usa para calcular el precio de liquidación de las sombras y
de la cuenta paper; cambiarlo cambia cuándo se liquidan las sombras. El resto es investigación aislada: se puede tocar sin
afectar la medición (aunque cambiar un criterio de descarte tras ver resultados rompe el pre-registro de la Fase 2). Ver
[Sombras](../ZONAS_PROTEGIDAS.md#sombras).

## Cómo se conecta

- Usa las mismas estrategias (`app/strategies`), el mismo motor de SL/TP (`app/trading/stop_engine.py`) y las mismas fórmulas de
  PnL (`app/execution/pnl.py`) que el bot en vivo: una estrategia se comporta igual en ambos.
- Lee `ohlcv_cache`, `funding_cache`, `contract_specs_cache` y `asset_universe`; escribe `backtest_trades`,
  `backtest_skipped_entries`, `backtest_runs` y `backtest_verdicts`.
- La API (`/api/backtest/verdicts`) y varios scripts de la Fase 2 leen los veredictos.

## Archivos

### `app/backtesting/__init__.py`

**Responsabilidad.** Docstring del paquete: investigación offline que no alimenta la medición.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/backtesting/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting.html`](../api/app/backtesting.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/backtesting/__init__.py -->

### `app/backtesting/engine.py`

**Responsabilidad.** El motor de backtest *event-driven*: una posición a la vez por (estrategia, símbolo, timeframe).

**Funciones y clases clave.**
- `run_backtest()` — simula una estrategia sobre un símbolo y timeframe, sin red, y devuelve las operaciones (`RawTrade`) y las
  entradas omitidas (`RawSkip`).

**Invariantes — no romper.**
- **Sin sesgo de anticipación:** la señal se evalúa con la vela `i` cerrada y se llena en el OPEN de la vela `i+1`. El slippage
  es un **costo plano en USDT**, no un cambio de precio, para que el stress-test de comisiones y slippage sea una simple
  recomputación posterior (`metrics.py`).
- Liquidación con `MARK_PRICE`; SL y TP con `LAST_PRICE`. La vela de **entrada** también se evalúa contra SL/TP/liquidación/funding.
- Orden de eventos adversos por cercanía de precio (SL vs liquidación) y, si el OPEN ya está más allá de un umbral (gap), se
  ejecuta a ese OPEN: nunca se asume un fill en un precio que no se transó. El trailing actualizado en una vela solo puede
  disparar en una vela **posterior**.
- Tope de riesgo por operación: si el SL implicaría perder más de `MAX_SL_MARGIN_LOSS_PCT` del margen, la entrada se omite y se
  registra en `skipped`.
- **Ya no descarga nada por red:** lee de la caché con `get_cached_or_raise` y lanza `MissingHistoricalDataError` si faltan velas.

**Trampas conocidas.**
- `MAX_LOOKBACK_BARS = 300`: nunca le pasa más historia a `evaluate` por vela (fue la causa del bug de la estrategia por
  percentiles, ver [strategies](strategies.md#appstrategiesfunding_contrarian_percentilepy)).
- Lo que antes se diagnosticó como un «cuelgue de red» en una corrida completa era cómputo lento sin logging mezclado con
  descargas repetidas de rangos ya cacheados (ver `docs/historial/FASE2_BLOQUEO_RED.md`).

<!-- hechos:app/backtesting/engine.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting/engine.html`](../api/app/backtesting/engine.html)
- **Importa (internos):** 14 módulos (backtesting 2, config 1, core 1, execution 1, market 1, persistence 4, strategies 1, trading 3)
- **Lo importan:** `app.backtesting.metrics`, `app.backtesting.report`
- **Configuración que lee:** `BACKTEST_SLIPPAGE_BPS`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `MAX_SL_MARGIN_LOSS_PCT`, `TAKER_FEE_PCT`
- **Tablas que lee:** `contract_specs_cache`, `funding_cache`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backtest_engine.py`, `tests/unit/test_backtest_metrics.py`, `tests/unit/test_backtest_report.py`, `tests/unit/test_backtest_sanity.py`, `tests/unit/test_funding_contrarian_percentile_strategy.py`, `tests/unit/test_portfolio_simulation.py`, `tests/unit/test_risk_analysis.py`, `tests/unit/test_sl_calc.py`, `tests/unit/test_slippage_and_funding_accrual.py`, `tests/unit/test_stop_engine.py`
<!-- /hechos:app/backtesting/engine.py -->

### `app/backtesting/funding.py`

**Responsabilidad.** La serie de funding alineada a las velas del backtest y el costo de funding por vela.

**Funciones y clases clave.**
- `build_funding_series()` — alinea el funding real con las velas, **aproximando donde no hay dato**;
  `funding_cost_for_bar()` — costo de una vela con la tasa prorrateada por su duración.

**Invariantes — no romper.** Simplificación documentada: el funding se **prorratea linealmente** por la duración de la vela
respecto del `fundingInterval`, en vez de aplicarlo en los horarios reales de *settlement*. Sin dato real (antes de ~2024) se usa la
**mediana** del funding real observado del símbolo y se marca `is_approximated=True`.

**Trampas conocidas.** Esa marca es la que impide que `funding_contrarian_percentile_experimental` opere con dato inventado.

<!-- hechos:app/backtesting/funding.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting/funding.html`](../api/app/backtesting/funding.html)
- **Importa (internos):** `app.persistence.models`
- **Lo importan:** `app.backtesting.engine`, `app.trading.signal_generator`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backtest_funding.py`
<!-- /hechos:app/backtesting/funding.py -->

### `app/backtesting/liquidation.py`

**Responsabilidad.** El precio de liquidación en **margen aislado**.

**Funciones y clases clave.**
- `compute_liquidation_price()` — LONG: `entrada × (1 − 1/leverage + MMR)`; SHORT: `entrada × (1 + 1/leverage − MMR)`;
  `maintenance_margin_rate_for_notional()` — el MMR del tramo de `position_tiers` que corresponde al nocional.

**Invariantes — no romper.**
- Aproxima el margen de mantenimiento sobre el nocional de **entrada** (simplificación estándar, documentada).
- Sin tramos disponibles usa un MMR de respaldo conservador (`DEFAULT_MMR_FALLBACK = 0.005`).
- Se evalúa con `MARK_PRICE`, no con `LAST_PRICE`.

**Trampas conocidas.** Lo usan también las sombras y la cuenta paper (vía `app/trading/levels.py`).

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/backtesting/liquidation.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting/liquidation.html`](../api/app/backtesting/liquidation.html)
- **Importa (internos):** `app.persistence.models`
- **Lo importan:** `app.backtesting.engine`, `app.trading.levels`, `app.trading.position_monitor`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_liquidation.py`
<!-- /hechos:app/backtesting/liquidation.py -->

### `app/backtesting/metrics.py`

**Responsabilidad.** Las métricas agregadas sobre las operaciones del backtest.

**Funciones y clases clave.**
- `compute_metrics()` (winrate, profit factor, PnL, drawdown, esperanza), `profit_factor_only()`, `split_is_oos()`,
  `walk_forward_folds()`, `concentration_pct()`, `stressed_profit_factor()` (multiplica comisiones y slippage **sin re-simular**),
  `benchmark_buy_and_hold()`, `pct_symbols_with_pf_gt1()`, `pct_folds_positive()` y `margin_loss_distribution()`.
- `simulate_portfolio()` y `simulate_portfolio_monte_carlo()` — una cuenta compartida por todos los símbolos de una estrategia,
  barajando el desempate de `entry_time` con semilla fija.

**Invariantes — no romper.** La simulación de cartera es **informativa**: no participa en los criterios de descarte.

**Trampas conocidas.** El drawdown *mark-to-market* es una aproximación barata (interpola linealmente el PnL flotante entre apertura y
cierre).

<!-- hechos:app/backtesting/metrics.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting/metrics.html`](../api/app/backtesting/metrics.html)
- **Importa (internos):** `app.backtesting.engine`
- **Lo importan:** `app.backtesting.report`, `app.trading.real_money_report`, `scripts.analyze_risk`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backtest_metrics.py`, `tests/unit/test_backtest_sanity.py`, `tests/unit/test_portfolio_simulation.py`
<!-- /hechos:app/backtesting/metrics.py -->

### `app/backtesting/report.py`

**Responsabilidad.** Orquesta el backtest completo: corre cada estrategia × símbolo × timeframe, persiste cada corrida y calcula el
veredicto global por estrategia aplicando los criterios de descarte.

**Funciones y clases clave.**
- `run_full_backtest()` — primero **borra** los resultados anteriores (`backtest_repo.clear_results`), de modo que reintentar tras
  una interrupción es idempotente; cada celda se parte en IS y OOS (el OOS es el último `BACKTEST_OOS_SPLIT_PCT`).
- `_evaluate_discard_criteria()` y `_build_verdict()` — aplican los criterios; `StrategyResult`.

**Invariantes — no romper.**
- Los parámetros y criterios deben quedar **commiteados antes** de ejecutar esto: «tras ver resultados OOS no se modifican
  parámetros ni criterios» (`docs/historial/FASE2_CRITERIOS.md`).
- Las estrategias experimentales no participan en el veredicto de descarte; solo se reportan.

**Trampas conocidas.** `BACKTEST_OFFICIAL_END_DATE` está **congelada** para que dos corridas den exactamente los mismos números.

<!-- hechos:app/backtesting/report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting/report.html`](../api/app/backtesting/report.html)
- **Importa (internos):** `app.backtesting.engine`, `app.backtesting.metrics`, `app.config`, `app.core.logging`, `app.market.ohlcv_history`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.backtest_repo`, `app.strategies.registry`
- **Lo importan:** `scripts.run_backtest`
- **Configuración que lee:** `BACKTEST_CONCENTRATION_LIMIT_PCT`, `BACKTEST_INITIAL_CAPITAL`, `BACKTEST_MAX_DRAWDOWN_PCT`, `BACKTEST_MIN_PCT_FOLDS_POSITIVE`, `BACKTEST_MIN_PCT_SYMBOLS_PF_GT1`, `BACKTEST_MIN_PROFIT_FACTOR`, `BACKTEST_MIN_TRADES_PER_CELL`, `BACKTEST_MIN_TRADES_TOTAL`, `BACKTEST_OOS_SPLIT_PCT`, `BACKTEST_PORTFOLIO_SIM_RUNS`, `BACKTEST_STRESS_FEE_MULTIPLIER`, `BACKTEST_STRESS_SLIPPAGE_MULTIPLIER`, `BACKTEST_WALK_FORWARD_FOLD_MONTHS`, `BACKTEST_WALK_FORWARD_STEP_MONTHS`, `DEFAULT_MARGIN_USDT`, `MAX_SIMULTANEOUS_POSITIONS`, `backtest_control_symbols_list`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** `backtest_runs`, `backtest_skipped_entries`, `backtest_trades`, `backtest_verdicts`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backtest_report.py`, `tests/unit/test_run_full_backtest_smoke.py`
<!-- /hechos:app/backtesting/report.py -->

### `app/backtesting/risk_analysis.py`

**Responsabilidad.** El análisis de riesgo de ruina con *bootstrap* sobre las operaciones OOS del backtest.

**Funciones y clases clave.**
- `run_full_grid()` — la grilla margen × posiciones simultáneas × tope de SL con un solo generador aleatorio;
  `run_bootstrap_scenario()`, `run_sensitivity_grid()` (reescala el pool a un profit factor objetivo) y
  `filter_trades_by_sl_cap()` (excluye operaciones **enteras**).
- `pct_returns_from_trades()`, `rescale_returns_to_target_pf()` y los formateadores Markdown.

**Invariantes — no romper.**
- El tope de SL es **exclusión, no truncamiento**: una operación se simula entera o se excluye entera (la versión anterior
  truncaba y hacía «un tope más ajustado nunca aumenta la ruina» cierto por construcción).
- La **ruina** es que el capital caiga por debajo del margen de la grilla. Un escenario con 0 operaciones elegibles ya no lanza:
  es una fila válida con `None` («n/a»).

**Trampas conocidas.**
- **No usar la columna de posiciones simultáneas para justificar `MAX_SIMULTANEOUS_POSITIONS`:** la concurrencia por lotes mide el
  drawdown solo al cierre del lote y trata las operaciones como independientes aunque las altcoins se muevan juntas; la tabla hace
  parecer que más posiciones es menos riesgo, al revés de lo esperable. El riesgo de correlación necesita su propio límite
  (`MAX_SAME_DIRECTION_POSITIONS`).
- El *bootstrap* i.i.d. (`block_size=1`) subestima las rachas; `block_size > 1` remuestrea bloques consecutivos como alternativa.
- *Fallo real que motivó una corrección:* `ema_cross_9_21` y las estrategias de funding usan el SL de respaldo (5 % a 10x = 50 % del
  margen en el 100 % de sus operaciones); un tope de 30 % las dejaba con 0 operaciones y `ValueError` tumbaba `scripts/analyze_risk.py`
  a mitad de camino. La grilla por defecto es `(0.40, 0.50, inf)`.
- Las probabilidades de dos filas con el mismo pool pueden diferir levemente (cada fila consume otro tramo del muestreo): es ruido de
  Monte Carlo, no un error.

<!-- hechos:app/backtesting/risk_analysis.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/backtesting/risk_analysis.html`](../api/app/backtesting/risk_analysis.html)
- **Importa (internos):** ninguno
- **Lo importan:** `scripts.analyze_risk`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_risk_analysis.py`
<!-- /hechos:app/backtesting/risk_analysis.py -->
