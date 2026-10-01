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
