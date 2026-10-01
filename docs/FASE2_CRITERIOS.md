# Fase 2 — Criterios y parámetros fijados ANTES de ejecutar el backtest completo

> Este documento se commitea **antes** de correr el backtest real contra la red (punto 8 de los ajustes de Fase 2). Regla de proceso (punto 5): **una vez vistos los resultados OOS, no se modifican parámetros ni criterios** — cualquier ajuste posterior se valida con paper trading en vivo (Fase 3), nunca re-corriendo el backtest con reglas distintas para forzar un resultado.

## Parámetros fijos por estrategia (literatura estándar, sin optimizar)

| Estrategia | Archivo | Parámetros fijos |
|---|---|---|
| `ema_cross_9_21` | `app/strategies/ema_cross.py` | EMA rápida 9, EMA lenta 21. Timeframe: 4h. |
| `trend_atr_stop_9_21_50` | `app/strategies/trend_atr_stop.py` | Filtro EMA 50, disparo cruce EMA 9/21, SL y trailing a 2.5×ATR(14). Timeframe: 4h. |
| `mean_reversion_rsi14_bb20` | `app/strategies/mean_reversion_rsi_bb.py` | RSI(14), sobrecompra/sobreventa 70/30, Bollinger(20, 2σ), TP en banda media, SL a 2.0×ATR(14). Timeframe: 1h. |
| `donchian_breakout_20` | `app/strategies/donchian_breakout.py` | Canal de 20 periodos, SL en el punto medio del canal. Timeframes: 4h **y** 1d (ambos se prueban como combos independientes). |
| `funding_contrarian_experimental` | `app/strategies/funding_contrarian.py` | Lookback 8 periodos de funding, umbral 0.03%/periodo. Timeframe: 4h. **EXPERIMENTAL — no participa en el veredicto de descarte** (historia real de funding más corta que el resto, ~2.3 años vs. ~4.5). |

SL/TP de respaldo cuando una estrategia no define uno propio: `BACKTEST_FALLBACK_SL_PCT=5%`, `BACKTEST_FALLBACK_TP_PCT=10%` (`app/config.py`).

## Tope de riesgo por operación (punto 1)

`MAX_SL_MARGIN_LOSS_PCT = 50%`: si el SL de una señal implicaría perder más del 50% del margen (a 10x, es decir, un movimiento de precio > 5%), la entrada se omite (no se abre, no se fuerza un SL más ajustado) y queda registrada en `backtest_skipped_entries`. Se reporta la distribución del % de margen perdido en los cierres por SL/trailing (media, mediana, máximo) por estrategia y activo.

## Capital de referencia (punto 9)

`BACKTEST_INITIAL_CAPITAL = 100 USDT` (configurable en `.env` — ver nota sobre `.env.example` en PROGRESS.md). Cada operación usa un margen fijo de `DEFAULT_MARGIN_USDT = 10 USDT` a `LEVERAGE = 10x` (igual que `PaperBackend`, sin compounding): el capital de 100 USDT es la referencia para calcular drawdown % y curva de equity, no un tamaño de posición que varía con el resultado acumulado.

## Criterios de descarte (OOS agregado) — punto 2

Una estrategia (no experimental, con evidencia suficiente) se **descarta** si falla **cualquiera** de estos criterios, evaluados sobre el periodo OOS (el 30% más reciente del rango histórico total, ver "Diseño del split" abajo):

| Criterio | Umbral | Variable en `.env` |
|---|---|---|
| Profit factor OOS agregado | `>= 1.2` | `BACKTEST_MIN_PROFIT_FACTOR` |
| % de símbolos con PF > 1 individual | `>= 50%` | `BACKTEST_MIN_PCT_SYMBOLS_PF_GT1` |
| % de folds walk-forward con resultado neto positivo | `>= 50%` | `BACKTEST_MIN_PCT_FOLDS_POSITIVE` |
| PF tras duplicar fees Y slippage (prueba de estrés) | `> 1.0` | `BACKTEST_STRESS_FEE_MULTIPLIER` / `_SLIPPAGE_MULTIPLIER = 2.0` |
| Drawdown máximo OOS | `<= 50%` | `BACKTEST_MAX_DRAWDOWN_PCT` |
| Concentración (1 operación o 1 símbolo) | `<= 40%` del PnL neto total | `BACKTEST_CONCENTRATION_LIMIT_PCT` |
| Operaciones totales (todos los símbolos, IS+OOS) | `>= 100`, si no: `evidencia_insuficiente` (ni aprobada ni descartada) | `BACKTEST_MIN_TRADES_TOTAL` |
| Mínimo por celda (estrategia, símbolo, fold) para contar en el % de folds | `>= 30` operaciones | `BACKTEST_MIN_TRADES_PER_CELL` |

El benchmark buy-and-hold se calcula y reporta por celda (estrategia × símbolo × segmento) en `backtest_runs.benchmark_return_pct` / `benchmark_max_drawdown_pct`, para comparación visual — no es, por sí solo, un criterio de descarte automático adicional a los de la tabla (los seis de arriba ya determinan la decisión; el benchmark es contexto para que tú lo interpretes).

`funding_contrarian_experimental` nunca se descarta por estos criterios (se reporta igual, marcado explícitamente como experimental).

## Diseño del split IS/OOS y folds (punto 5 — divulgación explícita)

- **OOS es un único periodo contiguo**: el 30% más reciente (`BACKTEST_OOS_SPLIT_PCT=0.30`) del rango histórico completo (~2022-04-17 a hoy). No son múltiples hold-outs independientes.
- **Los folds de walk-forward SE SOLAPAN** (ventanas de 6 meses con paso de 2 meses, `BACKTEST_WALK_FORWARD_FOLD_MONTHS`/`_STEP_MONTHS`): son puramente diagnósticos (alimentan `pct_folds_positive`), no hold-outs independientes entre sí — una misma operación puede caer en 2-3 folds superpuestos.
- Combinación de ambos: el criterio de descarte usa el **OOS agregado** (hold-out real) más el **% de folds positivos** (diagnóstico de consistencia temporal) — ninguno sustituye al otro.

## Control de sesgo de supervivencia (punto 3)

El universo de hoy (top-10 CoinGecko actual) puede no reflejar qué activos existían/eran líquidos en 2022. Se documenta esto como limitación, y se reporta un **grupo de control** restringido a `BTCUSDT` y `ETHUSDT` (`BACKTEST_CONTROL_SYMBOLS`) — los dos activos con continuidad histórica indiscutible en todo el rango — calculando el PF OOS solo sobre ese subconjunto (`BacktestVerdict.pf_control_group`), para contrastar contra el PF del universo completo. Por eficiencia, el grupo de control se calcula filtrando los mismos trades ya simulados (BTC/ETH siempre se incluyen en la corrida completa, estén o no en el top-10 del día), no como una corrida separada — resultado idéntico, sin red adicional.

## Funding real vs. aproximado (punto 4)

Se reportan tres cifras de PF por estrategia: `pf_full_period_approx` (todo el periodo OOS, con funding aproximado antes de ~2024-06), `pf_real_funding_only` (solo operaciones cuyo funding fue 100% real) y `pf_oos_aggregate` (el que decide el veredicto, igual a `pf_full_period_approx`). **Si `pf_real_funding_only` y `pf_full_period_approx` discrepan en si superan el umbral de PF, prevalece `pf_real_funding_only`** para la decisión final — se documenta explícitamente cuándo ocurre esto en el reporte.

## Detalles de ejecución (punto 6)

- Se descarta la última vela si todavía no cerró (`ohlcv_history.drop_incomplete_last_bar`).
- Liquidación evaluada con `MARK_PRICE`; SL/TP con `LAST_PRICE` (son series distintas, verificado — ver `docs/FASE2_PLAN.md`).
- Se reporta PnL bruto y neto por separado en cada celda (`backtest_runs.pnl_gross_total_usdt` / `pnl_net_total_usdt`).
- Llamadas a CoinGecko por categoría usan `per_page=250` (máximo) para no perder exclusiones por paginación.
- `UNIVERSE_MANUAL_EXCLUSIONS` (ids de CoinGecko, vacío por defecto) permite excluir manualmente cualquier activo que el filtro automático de categorías no capture.
- Chequeo de sanidad: si el precio de CoinGecko y el de Bitunix para el mismo símbolo difieren más de `UNIVERSE_PRICE_SANITY_TOLERANCE_PCT` (5% por defecto), el símbolo se excluye del universo y se registra la alerta (evita un mapeo de ticker incorrecto).

## Verificación exitosa (punto 7)

El criterio de éxito de esta fase es que **el pipeline corra de extremo a extremo y los resultados queden auditables** en `backtest_trades` / `backtest_runs` / `backtest_verdicts` — **no** que exista una estrategia ganadora. Si ninguna estrategia sobrevive los criterios de la tabla de arriba, se reporta así honestamente en `PROGRESS.md`, sin relajar ningún umbral para forzar un "ganador".

## Correcciones de motor previas a la corrida completa

Tras revisar los logs de los intentos previos de corrida completa, se determinó que el diagnóstico de "bloqueo de red" era incorrecto: todas las respuestas de Bitunix fueron 200 OK; los "colgados" observados eran en realidad **cómputo lento sin logging** (el motor recalculaba indicadores desde cero en cada vela, sin ninguna señal de progreso) mezclado con **descargas redundantes** de rangos ya cacheados. Lo siguiente se corrigió ANTES de ejecutar el backtest completo — son correcciones de motor/infraestructura, **no** cambios a los criterios o parámetros de estrategias de las secciones anteriores (que permanecen congelados sin modificar):

1. **Descarga incremental y reanudable**: `app/market/ohlcv_history.py::download_missing` y `app/market/funding_history.py::download_missing_funding` descargan SOLO el rango faltante (cola reciente / cabeza vieja respecto a lo ya cacheado), guardan cada página de inmediato (reanudable sin perder progreso) y reintentan una página vacía antes de aceptarla como fin real del historial. Vive en un script separado, `scripts/download_history.py`, ejecutado por el usuario antes del backtest.
2. **El motor ya no toca la red**: `app/backtesting/engine.py::run_backtest` lee exclusivamente de `ohlcv_cache`/`funding_cache` (`ohlcv_history.get_cached_or_raise`, `funding_repo.get_funding`) y lanza `MissingHistoricalDataError` con un mensaje claro si faltan velas, en vez de descargar nada silenciosamente. `scripts/run_backtest.py` ya no construye el universo ni refresca specs por red — los lee de SQLite.
3. **Vela de entrada evaluada**: una posición abierta en el `open` de la vela `i` también se evalúa contra SL/TP/liquidación/funding en el resto del rango de esa misma vela (antes solo se evaluaba desde la vela `i+1`).
4. **Orden SL vs. liquidación por cercanía de precio**: entre el stop loss y la liquidación, se evalúa primero el umbral más cercano al precio de entrada (el que el precio alcanzaría primero al moverse en contra de forma monótona) — ya no se asume que la liquidación siempre se revisa antes que el SL.
5. **Ejecución al OPEN si hay gap**: si una vela abre ya más allá de un umbral (SL, liquidación o TP), el cierre se ejecuta a ese OPEN, no al precio nominal del umbral (que nunca se transó).
6. **Señales opuestas con posición abierta — comportamiento explícito**: el motor **ignora** cualquier señal mientras hay una posición abierta (solo SL/TP/trailing/liquidación/fin de datos la cierran); una señal opuesta nunca cierra ni invierte la posición. Para `mean_reversion_rsi14_bb20` esto coincide con lo descrito en el plan original (su salida es la banda media / SL por ATR, no la señal contraria). Para `ema_cross_9_21` esto es una decisión explícita de esta corrección (antes implícita, sin documentar): como estrategia de referencia/baseline, su gestión de riesgo es el SL/TP porcentual de respaldo (`BACKTEST_FALLBACK_SL_PCT`/`_TP_PCT`), no un cierre-e-inversión en cada cruce contrario.
7. **Indicadores precalculados una sola vez por serie**: `BaseStrategy.precompute()` (y las 4 estrategias que usan indicadores de `app/indicators/engine.py`) calculan EMA/RSI/ATR/Bollinger/Donchian una sola vez sobre toda la serie en vez de recalcularlos en cada vela sobre una ventana — mismo resultado exacto (son todos causales), medido ~2.5x más rápido en `ema_cross` sobre ~9760 velas de 4h. Verificado con un test que compara trade-a-trade contra el camino de respaldo (recálculo por vela).
8. **Logging de progreso**: una línea por celda (estrategia × símbolo × timeframe) al empezar y al terminar con duración, y una línea cada ~20% de las velas procesadas dentro de una celda.
9. **`funding_contrarian_percentile_experimental`** (`app/strategies/funding_contrarian_percentile.py`): estrategia nueva y separada de `funding_contrarian_experimental`, con un umbral RELATIVO (percentil de la distribución reciente de funding del propio símbolo) en vez de un valor absoluto fijo — también marcada EXPERIMENTAL, con su propio historial en `backtest_runs`/`backtest_verdicts`, sin afectar en nada a la estrategia original. Ver la corrección del punto 11 más abajo: su primera versión tenía un bug real que la dejaba siempre en 0 señales.

## Correcciones de la segunda revisión (misma fase, después de la primera corrida completa)

La corrida completa con las correcciones de arriba terminó sin problemas de red (207.9s, resultado auditable en `backtest.log`). Una segunda revisión encontró los siguientes problemas reales, corregidos antes de volver a correr:

10. **Simulación de cartera única (informativa)**: `app/backtesting/metrics.py::simulate_portfolio` simula UNA sola cuenta compartida entre todos los símbolos/timeframes de una estrategia — capital inicial (`BACKTEST_INITIAL_CAPITAL`), tope de `MAX_SIMULTANEOUS_POSITIONS` posiciones simultáneas, margen fijo por operación, capital nunca negativo (se trunca en 0) — en vez del supuesto irreal de capital/margen ilimitado que usa cada celda por separado. Se reporta como `BacktestVerdict.portfolio_max_drawdown_pct` / `portfolio_concentration_pct` / `portfolio_final_capital_usdt` / `portfolio_trades_included` / `portfolio_trades_skipped_no_margin` — **informativo, no participa en los criterios de descarte** de la sección "Criterios de descarte" (esos siguen siendo los definidos arriba, sin cambios). Limitación documentada: los trades se generaron de forma independiente por símbolo/timeframe (sin saber de esta cuenta compartida), así que omitir uno aquí no afecta a los demás — es una aproximación razonable para una cifra informativa, no una re-simulación completa.
11. **BUG real corregido en `funding_contrarian_percentile_experimental`**: la primera versión exigía `len(df) >= history_window + 1` (721 velas) dentro de `evaluate()`, pero el motor nunca pasa más de `MAX_LOOKBACK_BARS` (300) velas por vela evaluada — esa condición nunca se cumplía, así que la estrategia SIEMPRE devolvía HOLD. Las "0 señales" reportadas originalmente para esta estrategia **no eran un hallazgo legítimo — eran este bug**. (Nota aparte: `funding_contrarian_experimental`, la original de umbral fijo, sí generó señales reales en la corrida completa — 154 operaciones, PF OOS 1.43 — así que la preocupación inicial de "umbral inalcanzable" tampoco aplicó a ella en la práctica; esa estrategia no tenía ningún bug.) Corrección: igual que las demás estrategias con indicadores, los percentiles móviles y el promedio reciente de funding se calculan UNA SOLA VEZ sobre toda la serie en `precompute()` (causal, `rolling().quantile()`); `evaluate()` solo lee las columnas ya calculadas, sin que le importe cuántas filas reciba. Verificado con un test de integración que reproduce exactamente el bug (750 velas, `history_window` default 720, señal evaluada con un sub-dataframe truncado a 300 filas) y confirma que ahora SÍ genera una operación.
12. **Funding aproximado (pre-~2024) en `funding_contrarian_percentile_experimental`**: antes de que exista historial real de funding, `build_funding_series` rellena con la mediana constante del funding real observado — en ese tramo, cualquier percentil calculado sobre la ventana de referencia es degenerado (distribución casi constante). Decisión: la estrategia NO emite señales mientras la ventana de referencia (`history_window` velas) contenga aunque sea una vela de funding aproximado — exige funding 100% real en toda la ventana, no solo en la vela actual. Esto reduce aún más su historia útil (ya documentada como más corta, ~2.3 años) pero evita señales basadas en una distribución fabricada.
13. **Integridad de caché**: `scripts/download_history.py` ahora compara, por cada serie descargada, el número de velas esperadas (según el rango y el piso real detectado) contra las realmente guardadas, y registra como advertencia cualquier hueco interno (velas faltantes dentro del rango ya cubierto, p. ej. por una interrupción del exchange). El motor (`app/backtesting/engine.py::run_backtest`) registra cuántas velas se descartan al alinear LAST_PRICE con MARK_PRICE por celda (deberían coincidir casi siempre; un número alto señalaría un problema de datos).
