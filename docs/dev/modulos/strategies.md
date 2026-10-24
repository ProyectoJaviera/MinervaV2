# Guía del paquete `strategies`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/strategies.html`](../api/app/strategies.html)

## Qué hace el paquete

Las **estrategias por reglas**: la única fuente de señales del bot. Una estrategia mira las velas cerradas de un símbolo y
dice `LONG`, `SHORT` o `HOLD`; opcionalmente propone sus propios SL, TP y trailing. **No abre nada**: el generador de
señales (`app/trading/signal_generator.py`) la registra, se simula una operación sombra y el LLM la etiqueta; la cuenta
paper solo abre las aprobadas a tiempo. El LLM revisa las señales, no las inventa.

## Zona protegida

**Sí, todo el paquete.** Los parámetros están fijados por el pre-registro de la Fase 2 y no se tocan durante la medición:
agregar, quitar o renombrar una estrategia, o cambiar un umbral, cambia qué señales nacen, es decir **qué se mide**. Ver
[ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#estrategias-y-parámetros). Una estrategia nueva se prueba primero en el backtest
y se incorpora a la medición solo con una medición nueva.

## Las seis estrategias vigentes

| Nombre (clave del registro) | Timeframes | Disparo | SL propio | TP propio | Trailing | Elegible en la cuenta paper¹ |
|---|---|---|---|---|---|---|
| `ema_cross_9_21` | 4h | cruce de EMA(9) sobre EMA(21), hacia arriba (LONG) o abajo (SHORT) | no (respaldo %) | no | no | sí |
| `trend_atr_stop_9_21_50` | 4h | el mismo cruce, solo a favor de la EMA(50) | 2,5 × ATR(14) | no | 2,5 × ATR | no |
| `mean_reversion_rsi14_bb20` | 1h | RSI(14) < 30 y cierre ≤ banda inferior (LONG); RSI > 70 y cierre ≥ superior (SHORT) | 2 × ATR(14) | banda media (SMA20) | no | no |
| `donchian_breakout_20` | 4h y 1d | el cierre rompe el máximo (LONG) o el mínimo (SHORT) de las 20 velas **previas** | punto medio del canal | no | no | no |
| `funding_contrarian_experimental` | 4h | funding medio de los últimos 8 periodos sobre +0,03 % y momentum negativo (SHORT), o bajo −0,03 % y momentum positivo (LONG) | no | no | no | sí (experimental) |
| `funding_contrarian_percentile_experimental` | 4h | igual, pero con umbral relativo: percentiles 90 y 10 de la ventana de 720 velas, y solo con funding 100 % real en toda la ventana | no | no | no | sí (experimental) |

¹ `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` (por defecto estas tres): solo ellas pueden abrir en la cuenta paper; las demás generan
señales y sombras igual. Las dos `funding_contrarian_*` son **experimentales**: no participan en el veredicto de descarte
del backtest, solo se reportan.

## Cómo se conecta

- `registry.py` crea una instancia de cada estrategia: `STRATEGIES` (nombre → instancia), `STRATEGY_TIMEFRAMES` (en qué
  timeframes corre cada una) y `EXPERIMENTAL_STRATEGIES`. Lo leen el generador de señales en vivo
  (`app/trading/signal_generator.py`), el informe del backtest (`app/backtesting/report.py`) y varios scripts de la Fase 2.
- `precompute(df)` agrega las columnas de indicadores **una vez** sobre toda la serie y `evaluate(df)` las usa si están
  presentes (si no, las calcula): el resultado es idéntico, pero el backtest no recalcula en cada vela.
- Los indicadores salen de `app/indicators/engine.py`.

## Archivos

### `app/strategies/__init__.py`

**Responsabilidad.** Docstring del paquete: las estrategias son la fuente de las señales y sus parámetros no se tocan
durante la medición.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/strategies/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies.html`](../api/app/strategies.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/strategies/__init__.py -->

### `app/strategies/base.py`

**Responsabilidad.** El contrato de una estrategia: la señal (`Signal`) y la clase base `BaseStrategy`.

**Funciones y clases clave.**
- `Signal` — enumeración `LONG`, `SHORT`, `HOLD`.
- `BaseStrategy.evaluate(df)` — **abstracto**: decide la señal para la **última** vela cerrada.
- `BaseStrategy.stop_price()`, `take_profit_price()` y `trailing_distance()` — opcionales; devuelven `None` por defecto y el
  motor usa entonces un SL/TP porcentual de respaldo.
- `BaseStrategy.precompute(df)` — opcional; agrega las columnas de indicadores a toda la serie.

**Invariantes — no romper.**
- `evaluate` mira solo velas **cerradas** y hasta la actual; jamás futuras.
- Una estrategia que sobreescribe `precompute` **también debe leer** la columna precalculada si está presente.
- `name` es único: es la clave del registro y de las tablas (`signals`, `shadow_trades`, `backtest_*`).

**Trampas conocidas.** `precompute` nació de una corrección de rendimiento posterior a la Fase 2: sin él el motor de
backtest recalculaba cada indicador desde cero en cada vela.

<!-- hechos:app/strategies/base.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/base.html`](../api/app/strategies/base.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.backtesting.engine`, `app.strategies.donchian_breakout`, `app.strategies.ema_cross`, `app.strategies.funding_contrarian`, `app.strategies.funding_contrarian_percentile`, `app.strategies.mean_reversion_rsi_bb`, `app.strategies.registry`, `app.strategies.trend_atr_stop`, `app.trading.levels`, `app.trading.signal_generator`, `app.trading.sl_calc`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/series.py`, `tests/e2e/test_series.py`, `tests/unit/test_backtest_engine.py`, `tests/unit/test_backtest_sanity.py`, `tests/unit/test_donchian_breakout_strategy.py`, `tests/unit/test_ema_cross_strategy.py`, `tests/unit/test_funding_contrarian_percentile_strategy.py`, `tests/unit/test_funding_contrarian_strategy.py`, `tests/unit/test_mean_reversion_strategy.py`, `tests/unit/test_signal_generator.py`, `tests/unit/test_sl_calc.py`, `tests/unit/test_trend_atr_stop_strategy.py`
<!-- /hechos:app/strategies/base.py -->

### `app/strategies/donchian_breakout.py`

**Responsabilidad.** Ruptura del canal de Donchian (momentum o breakout sistemático), en 4h y 1d.

**Funciones y clases clave.**
- `DonchianBreakoutStrategy.evaluate()` — LONG si el cierre supera el máximo de las `period` velas **previas** (el canal
  se desplaza una vela con `shift(1)`); SHORT si cae bajo el mínimo previo. `HOLD` con menos de `period + 2` velas.
- `DonchianBreakoutStrategy.stop_price()` — el punto medio del canal vigente.

**Invariantes — no romper.**
- El canal se compara **desplazado una vela**: comparar el cierre con un canal que ya lo incluye sería trivialmente falso.

**Trampas conocidas.** `donchian_channel` incluye la vela actual; el desplazamiento está aquí, no en el indicador.

<!-- hechos:app/strategies/donchian_breakout.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/donchian_breakout.html`](../api/app/strategies/donchian_breakout.html)
- **Importa (internos):** `app.indicators.engine`, `app.strategies.base`
- **Lo importan:** `app.strategies.registry`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_donchian_breakout_strategy.py`
<!-- /hechos:app/strategies/donchian_breakout.py -->

### `app/strategies/ema_cross.py`

**Responsabilidad.** Cruce de dos EMAs; la vigente es `ema_cross_9_21`. Sin SL/TP propios.

**Funciones y clases clave.**
- `EMACrossStrategy.evaluate()` — mide el cruce con la diferencia `rápida − lenta`: de ≤ 0 a > 0 es LONG, de ≥ 0 a < 0 es
  SHORT. `HOLD` con menos de 2 velas o sin cruce.

**Invariantes.** `fast < slow` (el constructor lanza `ValueError` si no). El nombre incluye los periodos
(`ema_cross_<fast>_<slow>`).

**Trampas conocidas.** Fue la estrategia de la vertical mínima de la Fase 1, antes del backtest.

<!-- hechos:app/strategies/ema_cross.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/ema_cross.html`](../api/app/strategies/ema_cross.html)
- **Importa (internos):** `app.indicators.engine`, `app.strategies.base`
- **Lo importan:** `app.strategies.registry`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backtest_engine.py`, `tests/unit/test_check_measurement_safety.py`, `tests/unit/test_ema_cross_strategy.py`
<!-- /hechos:app/strategies/ema_cross.py -->

### `app/strategies/funding_contrarian.py`

**Responsabilidad.** Contrarian de *funding* con umbral absoluto (0,03 % por periodo, ventana de 8 periodos). **Experimental**.

**Funciones y clases clave.**
- `FundingContrarianStrategy.evaluate()` — SHORT si el funding medio sobrepasa el umbral y el momentum es negativo; LONG al
  revés. Necesita la columna `funding_rate`; sin ella (o con NaN en la ventana) devuelve `HOLD`.

**Invariantes — no romper.**
- **Su umbral NO se toca** (pre-registro de la Fase 2: tras ver resultados fuera de muestra no se modifican parámetros).
  Por eso existe una variante nueva por percentiles en vez de editar esta.
- Es la única estrategia que no es solo de precio: el generador en vivo y el backtest le agregan `funding_rate`.

**Trampas conocidas.** La corrida real mostró que genera pocas señales porque el funding observado ronda 0,01 %.
Su historial útil de funding es más corto que el de las demás (el histórico de Bitunix llega solo hasta ~2024).

<!-- hechos:app/strategies/funding_contrarian.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/funding_contrarian.html`](../api/app/strategies/funding_contrarian.html)
- **Importa (internos):** `app.strategies.base`
- **Lo importan:** `app.strategies.registry`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_funding_contrarian_strategy.py`
<!-- /hechos:app/strategies/funding_contrarian.py -->

### `app/strategies/funding_contrarian_percentile.py`

**Responsabilidad.** Variante v2 **experimental** del contrarian de funding con umbral relativo (percentiles 90 y 10 de una
ventana de 720 velas).

**Funciones y clases clave.**
- `FundingContrarianPercentileStrategy.precompute()` — calcula **una vez** el promedio reciente del funding, los percentiles
  móviles (causales, `rolling().quantile()`) y la columna `funding_history_all_real`.
- `FundingContrarianPercentileStrategy.evaluate()` — solo lee columnas ya calculadas; SHORT si el promedio supera el
  percentil alto y el momentum es negativo, LONG al revés.

**Invariantes — no romper.**
- **No opera si la ventana de referencia contiene AUNQUE SEA una vela de funding aproximado** (antes de ~2024 el backtest
  rellena con la mediana constante y los percentiles serían degenerados). Se exige funding 100 % real en toda la ventana.
- Es una estrategia **nueva y distinta**, con su propio nombre y su propia historia en `backtest_runs`/`backtest_verdicts`.

**Trampas conocidas.**
- *Bug corregido:* la primera versión exigía `len(df) >= history_window + 1` dentro de `evaluate`, pero el motor de
  backtest nunca le pasa más de `MAX_LOOKBACK_BARS` (300) velas, así que **siempre devolvía HOLD** (0 señales); ese «0
  señales» se informó primero como si fuera un hallazgo. Se corrigió calculando todo en `precompute`.

<!-- hechos:app/strategies/funding_contrarian_percentile.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/funding_contrarian_percentile.html`](../api/app/strategies/funding_contrarian_percentile.html)
- **Importa (internos):** `app.strategies.base`
- **Lo importan:** `app.strategies.registry`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_funding_contrarian_percentile_strategy.py`
<!-- /hechos:app/strategies/funding_contrarian_percentile.py -->

### `app/strategies/mean_reversion_rsi_bb.py`

**Responsabilidad.** Reversión a la media en rango (familia distinta a la de tendencia), en 1h.

**Funciones y clases clave.**
- `MeanReversionRSIBBStrategy.evaluate()` — LONG con RSI < `oversold` (30) y cierre ≤ banda inferior; SHORT simétrico con
  RSI > `overbought` (70) y cierre ≥ banda superior.
- `take_profit_price()` — la banda media (SMA); `stop_price()` — `ATR_MULTIPLIER` (2,0) × ATR en contra.

**Invariantes.** `HOLD` con menos de `max(rsi_period, bb_period) + 1` velas. Nombre: `mean_reversion_rsi<rsi>_bb<bb>`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/strategies/mean_reversion_rsi_bb.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/mean_reversion_rsi_bb.html`](../api/app/strategies/mean_reversion_rsi_bb.html)
- **Importa (internos):** `app.indicators.engine`, `app.strategies.base`
- **Lo importan:** `app.strategies.registry`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_mean_reversion_strategy.py`
<!-- /hechos:app/strategies/mean_reversion_rsi_bb.py -->

### `app/strategies/registry.py`

**Responsabilidad.** El registro de las estrategias vigentes y de en qué timeframes corre cada una.

**Funciones y clases clave.**
- `STRATEGIES` — `nombre → instancia`; `STRATEGY_TIMEFRAMES` — `nombre → [timeframes]`; `EXPERIMENTAL_STRATEGIES` — las dos
  `funding_contrarian_*`.

**Invariantes — no romper.**
- Es la **fuente de verdad** de qué señales nacen. Agregar, quitar, renombrar o cambiar los parámetros con que se
  construye una entrada, o sus timeframes, cambia la medición (ver [ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#estrategias-y-parámetros)).
- Los nombres de `STRATEGY_TIMEFRAMES` y de `EXPERIMENTAL_STRATEGIES` deben ser los de `STRATEGIES`: una estrategia sin
  timeframes no se evalúa nunca, y un timeframe sin estrategia no hace nada.

**Trampas conocidas.** Donchian es la única que se evalúa en dos timeframes (4h y 1d).

<!-- hechos:app/strategies/registry.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/registry.html`](../api/app/strategies/registry.html)
- **Importa (internos):** `app.strategies.base`, `app.strategies.donchian_breakout`, `app.strategies.ema_cross`, `app.strategies.funding_contrarian`, `app.strategies.funding_contrarian_percentile`, `app.strategies.mean_reversion_rsi_bb`, `app.strategies.trend_atr_stop`
- **Lo importan:** `app.backtesting.report`, `app.trading.signal_generator`, `scripts.analyze_risk`, `scripts.download_history`, `scripts.run_backtest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/series.py`, `tests/unit/test_analyze_risk_script.py`, `tests/unit/test_run_backtest_script.py`, `tests/unit/test_run_full_backtest_smoke.py`
<!-- /hechos:app/strategies/registry.py -->

### `app/strategies/trend_atr_stop.py`

**Responsabilidad.** Seguimiento de tendencia con stop por ATR, en 4h.

**Funciones y clases clave.**
- `TrendATRStopStrategy.evaluate()` — LONG con cruce alcista de EMA(9)/EMA(21) con el cierre **sobre** la EMA(50); SHORT con
  cruce bajista con el cierre **bajo** ella. Ignora los cruces en contra de la tendencia mayor. `HOLD` con menos de
  `trend + 1` velas.
- `stop_price()` y `trailing_distance()` — ambos a `ATR_MULTIPLIER` (2,5) × el último ATR (chandelier exit).

**Invariantes.** `fast < slow < trend`. Parámetros fijos de literatura, **sin optimizar**. `None` si el ATR aún no está
definido.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/strategies/trend_atr_stop.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/strategies/trend_atr_stop.html`](../api/app/strategies/trend_atr_stop.html)
- **Importa (internos):** `app.indicators.engine`, `app.strategies.base`
- **Lo importan:** `app.strategies.registry`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_trend_atr_stop_strategy.py`
<!-- /hechos:app/strategies/trend_atr_stop.py -->
