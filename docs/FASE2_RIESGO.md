# Fase 2 — Análisis de riesgo de ruina (bootstrap)

> Generado por `scripts/analyze_risk.py`. Ver `app/backtesting/risk_analysis.py`
> Corrida **v2** (tope de SL con redondeo corregido y ventana oficial fija). La version v1, con el error, queda en `docs/FASE2_RIESGO_v1.md`. Ver `docs/FASE2_REEJECUCION.md`.
> para el método completo y las simplificaciones documentadas (exclusión —no
> truncamiento— por tope de SL, concurrencia por lotes, reescalado
> proporcional por margen, bootstrap i.i.d. por defecto con una variante por
> bloques). Capital inicial: 100 USDT.
> Esta tabla es la base empírica para los parámetros de gestión de riesgo
> propuestos en `docs/FASE3_PLAN.md` — no sustituye los criterios de
> descarte congelados de `docs/FASE2_CRITERIOS.md` (esos evalúan si una
> estrategia vale la pena correr; esto evalúa qué tan agresivos pueden ser
> los parámetros de riesgo de cualquier estrategia con un perfil de
> resultados parecido al observado).

## Por estrategia

### `ema_cross_9_21`

**ema_cross_9_21** -- 556 operaciones OOS, PF 0.97, esperanza por operación -0.10 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 556 | 0 | n/a (0 de 556 elegibles) | n/a |
| 5 | 1 | 50% | 1 | 0 | 556 | 0.4% | 59.4% |
| 5 | 1 | sin tope | 1 | 0 | 556 | 0.9% | 58.3% |
| 5 | 2 | 40% | 1 | 556 | 0 | n/a (0 de 556 elegibles) | n/a |
| 5 | 2 | 50% | 1 | 0 | 556 | 0.4% | 55.5% |
| 5 | 2 | sin tope | 1 | 0 | 556 | 0.8% | 56.9% |
| 5 | 3 | 40% | 1 | 556 | 0 | n/a (0 de 556 elegibles) | n/a |
| 5 | 3 | 50% | 1 | 0 | 556 | 0.9% | 52.5% |
| 5 | 3 | sin tope | 1 | 0 | 556 | 0.7% | 53.8% |
| 10 | 1 | 40% | 1 | 556 | 0 | n/a (0 de 556 elegibles) | n/a |
| 10 | 1 | 50% | 1 | 0 | 556 | 22.8% | 90.6% |
| 10 | 1 | sin tope | 1 | 0 | 556 | 22.9% | 90.4% |
| 10 | 2 | 40% | 1 | 556 | 0 | n/a (0 de 556 elegibles) | n/a |
| 10 | 2 | 50% | 1 | 0 | 556 | 20.9% | 85.5% |
| 10 | 2 | sin tope | 1 | 0 | 556 | 22.7% | 88.8% |
| 10 | 3 | 40% | 1 | 556 | 0 | n/a (0 de 556 elegibles) | n/a |
| 10 | 3 | 50% | 1 | 0 | 556 | 20.6% | 84.0% |
| 10 | 3 | sin tope | 1 | 0 | 556 | 21.3% | 84.2% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 556 | 0.5% | 54.6% |
| 5 | 3 | 50% | 5 | 0 | 556 | 1.5% | 60.4% |

### `trend_atr_stop_9_21_50`

**trend_atr_stop_9_21_50** -- 493 operaciones OOS, PF 1.03, esperanza por operación +0.03 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 164 | 329 | 0.0% | 1.5% |
| 5 | 1 | 50% | 1 | 0 | 493 | 0.0% | 8.2% |
| 5 | 1 | sin tope | 1 | 0 | 493 | 0.0% | 7.8% |
| 5 | 2 | 40% | 1 | 164 | 329 | 0.0% | 1.8% |
| 5 | 2 | 50% | 1 | 0 | 493 | 0.0% | 7.6% |
| 5 | 2 | sin tope | 1 | 0 | 493 | 0.0% | 7.5% |
| 5 | 3 | 40% | 1 | 164 | 329 | 0.0% | 2.1% |
| 5 | 3 | 50% | 1 | 0 | 493 | 0.0% | 7.8% |
| 5 | 3 | sin tope | 1 | 0 | 493 | 0.0% | 6.5% |
| 10 | 1 | 40% | 1 | 164 | 329 | 0.0% | 29.2% |
| 10 | 1 | 50% | 1 | 0 | 493 | 0.2% | 50.0% |
| 10 | 1 | sin tope | 1 | 0 | 493 | 0.2% | 47.8% |
| 10 | 2 | 40% | 1 | 164 | 329 | 0.0% | 27.6% |
| 10 | 2 | 50% | 1 | 0 | 493 | 0.2% | 48.0% |
| 10 | 2 | sin tope | 1 | 0 | 493 | 0.4% | 46.5% |
| 10 | 3 | 40% | 1 | 164 | 329 | 0.1% | 25.7% |
| 10 | 3 | 50% | 1 | 0 | 493 | 0.4% | 45.9% |
| 10 | 3 | sin tope | 1 | 0 | 493 | 0.4% | 44.2% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 493 | 0.0% | 7.5% |
| 5 | 3 | 50% | 5 | 0 | 493 | 0.0% | 17.0% |

### `mean_reversion_rsi14_bb20`

**mean_reversion_rsi14_bb20** -- 2737 operaciones OOS, PF 0.74, esperanza por operación -0.38 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 129 | 2608 | 0.0% | 28.3% |
| 5 | 1 | 50% | 1 | 0 | 2737 | 0.0% | 34.0% |
| 5 | 1 | sin tope | 1 | 0 | 2737 | 0.0% | 34.9% |
| 5 | 2 | 40% | 1 | 129 | 2608 | 0.0% | 28.1% |
| 5 | 2 | 50% | 1 | 0 | 2737 | 0.0% | 30.0% |
| 5 | 2 | sin tope | 1 | 0 | 2737 | 0.0% | 32.6% |
| 5 | 3 | 40% | 1 | 129 | 2608 | 0.0% | 27.5% |
| 5 | 3 | 50% | 1 | 0 | 2737 | 0.0% | 31.1% |
| 5 | 3 | sin tope | 1 | 0 | 2737 | 0.0% | 32.5% |
| 10 | 1 | 40% | 1 | 129 | 2608 | 1.9% | 80.4% |
| 10 | 1 | 50% | 1 | 0 | 2737 | 4.5% | 82.0% |
| 10 | 1 | sin tope | 1 | 0 | 2737 | 3.9% | 83.3% |
| 10 | 2 | 40% | 1 | 129 | 2608 | 2.9% | 81.1% |
| 10 | 2 | 50% | 1 | 0 | 2737 | 3.5% | 80.2% |
| 10 | 2 | sin tope | 1 | 0 | 2737 | 4.5% | 81.5% |
| 10 | 3 | 40% | 1 | 129 | 2608 | 2.9% | 77.6% |
| 10 | 3 | 50% | 1 | 0 | 2737 | 4.1% | 80.7% |
| 10 | 3 | sin tope | 1 | 0 | 2737 | 4.1% | 80.8% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 2737 | 0.0% | 30.9% |
| 5 | 3 | 50% | 5 | 0 | 2737 | 0.0% | 43.5% |

### `donchian_breakout_20`

**donchian_breakout_20** -- 749 operaciones OOS, PF 0.90, esperanza por operación -0.25 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 226 | 523 | 0.0% | 34.2% |
| 5 | 1 | 50% | 1 | 0 | 749 | 0.3% | 57.0% |
| 5 | 1 | sin tope | 1 | 0 | 749 | 0.0% | 57.1% |
| 5 | 2 | 40% | 1 | 226 | 523 | 0.0% | 33.8% |
| 5 | 2 | 50% | 1 | 0 | 749 | 0.1% | 52.0% |
| 5 | 2 | sin tope | 1 | 0 | 749 | 0.1% | 55.4% |
| 5 | 3 | 40% | 1 | 226 | 523 | 0.0% | 31.1% |
| 5 | 3 | 50% | 1 | 0 | 749 | 0.2% | 52.1% |
| 5 | 3 | sin tope | 1 | 0 | 749 | 0.2% | 52.2% |
| 10 | 1 | 40% | 1 | 226 | 523 | 6.6% | 76.8% |
| 10 | 1 | 50% | 1 | 0 | 749 | 18.4% | 89.3% |
| 10 | 1 | sin tope | 1 | 0 | 749 | 19.7% | 90.1% |
| 10 | 2 | 40% | 1 | 226 | 523 | 7.0% | 74.0% |
| 10 | 2 | 50% | 1 | 0 | 749 | 19.1% | 88.4% |
| 10 | 2 | sin tope | 1 | 0 | 749 | 18.7% | 88.9% |
| 10 | 3 | 40% | 1 | 226 | 523 | 6.9% | 73.5% |
| 10 | 3 | 50% | 1 | 0 | 749 | 17.4% | 86.0% |
| 10 | 3 | sin tope | 1 | 0 | 749 | 19.5% | 85.7% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 749 | 0.2% | 51.0% |
| 5 | 3 | 50% | 5 | 0 | 749 | 1.1% | 65.6% |

### `funding_contrarian_experimental`

**funding_contrarian_experimental (experimental)** -- 21 operaciones OOS, PF 1.80, esperanza por operación +2.08 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 21 | 0 | n/a (0 de 21 elegibles) | n/a |
| 5 | 1 | 50% | 1 | 0 | 21 | 0.0% | 1.4% |
| 5 | 1 | sin tope | 1 | 0 | 21 | 0.0% | 2.7% |
| 5 | 2 | 40% | 1 | 21 | 0 | n/a (0 de 21 elegibles) | n/a |
| 5 | 2 | 50% | 1 | 0 | 21 | 0.0% | 1.1% |
| 5 | 2 | sin tope | 1 | 0 | 21 | 0.0% | 1.6% |
| 5 | 3 | 40% | 1 | 21 | 0 | n/a (0 de 21 elegibles) | n/a |
| 5 | 3 | 50% | 1 | 0 | 21 | 0.0% | 0.9% |
| 5 | 3 | sin tope | 1 | 0 | 21 | 0.0% | 1.4% |
| 10 | 1 | 40% | 1 | 21 | 0 | n/a (0 de 21 elegibles) | n/a |
| 10 | 1 | 50% | 1 | 0 | 21 | 0.1% | 18.2% |
| 10 | 1 | sin tope | 1 | 0 | 21 | 0.1% | 19.6% |
| 10 | 2 | 40% | 1 | 21 | 0 | n/a (0 de 21 elegibles) | n/a |
| 10 | 2 | 50% | 1 | 0 | 21 | 0.1% | 14.1% |
| 10 | 2 | sin tope | 1 | 0 | 21 | 0.0% | 14.7% |
| 10 | 3 | 40% | 1 | 21 | 0 | n/a (0 de 21 elegibles) | n/a |
| 10 | 3 | 50% | 1 | 0 | 21 | 0.1% | 11.9% |
| 10 | 3 | sin tope | 1 | 0 | 21 | 0.0% | 11.8% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 21 | 0.0% | 0.8% |
| 5 | 3 | 50% | 5 | 0 | 21 | 0.0% | 0.5% |

### `funding_contrarian_percentile_experimental`

**funding_contrarian_percentile_experimental (experimental)** -- 290 operaciones OOS, PF 1.08, esperanza por operación +0.26 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 290 | 0 | n/a (0 de 290 elegibles) | n/a |
| 5 | 1 | 50% | 1 | 0 | 290 | 0.1% | 41.9% |
| 5 | 1 | sin tope | 1 | 0 | 290 | 0.1% | 40.9% |
| 5 | 2 | 40% | 1 | 290 | 0 | n/a (0 de 290 elegibles) | n/a |
| 5 | 2 | 50% | 1 | 0 | 290 | 0.2% | 39.5% |
| 5 | 2 | sin tope | 1 | 0 | 290 | 0.1% | 38.6% |
| 5 | 3 | 40% | 1 | 290 | 0 | n/a (0 de 290 elegibles) | n/a |
| 5 | 3 | 50% | 1 | 0 | 290 | 0.1% | 36.2% |
| 5 | 3 | sin tope | 1 | 0 | 290 | 0.1% | 35.3% |
| 10 | 1 | 40% | 1 | 290 | 0 | n/a (0 de 290 elegibles) | n/a |
| 10 | 1 | 50% | 1 | 0 | 290 | 10.7% | 81.7% |
| 10 | 1 | sin tope | 1 | 0 | 290 | 9.8% | 82.0% |
| 10 | 2 | 40% | 1 | 290 | 0 | n/a (0 de 290 elegibles) | n/a |
| 10 | 2 | 50% | 1 | 0 | 290 | 10.3% | 76.9% |
| 10 | 2 | sin tope | 1 | 0 | 290 | 9.2% | 77.1% |
| 10 | 3 | 40% | 1 | 290 | 0 | n/a (0 de 290 elegibles) | n/a |
| 10 | 3 | 50% | 1 | 0 | 290 | 9.2% | 72.4% |
| 10 | 3 | sin tope | 1 | 0 | 290 | 9.2% | 73.2% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 290 | 0.1% | 36.1% |
| 5 | 3 | 50% | 5 | 0 | 290 | 0.2% | 41.4% |


## Pool combinado (solo referencia — escenario con esperanza negativa)

**pool combinado (ema_cross_9_21, trend_atr_stop_9_21_50, mean_reversion_rsi14_bb20, donchian_breakout_20)** -- 4535 operaciones OOS, PF 0.85, esperanza por operación -0.28 USDT.

Combina las 4 estrategias no
experimentales en una sola distribución empírica — **no** representa a
ninguna estrategia real por sí sola, es una referencia de "qué tan mal
podría ir" si el mercado se parece al conjunto observado. Las tablas por
estrategia de arriba son las que informan decisiones específicas por
estrategia.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 1075 | 3460 | 0.0% | 26.7% |
| 5 | 1 | 50% | 1 | 0 | 4535 | 0.0% | 40.6% |
| 5 | 1 | sin tope | 1 | 0 | 4535 | 0.0% | 41.6% |
| 5 | 2 | 40% | 1 | 1075 | 3460 | 0.0% | 24.6% |
| 5 | 2 | 50% | 1 | 0 | 4535 | 0.0% | 40.1% |
| 5 | 2 | sin tope | 1 | 0 | 4535 | 0.0% | 38.5% |
| 5 | 3 | 40% | 1 | 1075 | 3460 | 0.0% | 24.9% |
| 5 | 3 | 50% | 1 | 0 | 4535 | 0.0% | 37.8% |
| 5 | 3 | sin tope | 1 | 0 | 4535 | 0.0% | 38.8% |
| 10 | 1 | 40% | 1 | 1075 | 3460 | 3.5% | 77.5% |
| 10 | 1 | 50% | 1 | 0 | 4535 | 9.7% | 84.0% |
| 10 | 1 | sin tope | 1 | 0 | 4535 | 10.3% | 85.0% |
| 10 | 2 | 40% | 1 | 1075 | 3460 | 4.2% | 75.0% |
| 10 | 2 | 50% | 1 | 0 | 4535 | 9.9% | 82.0% |
| 10 | 2 | sin tope | 1 | 0 | 4535 | 9.3% | 81.2% |
| 10 | 3 | 40% | 1 | 1075 | 3460 | 2.7% | 73.2% |
| 10 | 3 | 50% | 1 | 0 | 4535 | 8.9% | 81.7% |
| 10 | 3 | sin tope | 1 | 0 | 4535 | 8.4% | 80.8% |

## Sensibilidad: aislando la deriva del efecto de margen/posiciones

Mismo pool combinado, filtrado primero al tope de SL del
50% (0
operaciones excluidas de 4535), con las pérdidas
reescaladas para alcanzar exactamente el profit factor objetivo de cada
fila (las ganancias no se tocan) — separa cuánto del riesgo de ruina viene
de la deriva negativa de las estrategias evaluadas de cuánto viene,
estructuralmente, del margen y las posiciones simultáneas elegidas, para
cualquier estrategia con un perfil de resultados parecido:

| PF objetivo | Margen (USDT) | Máx. posiciones simultáneas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|
| 1.0 | 5 | 1 | 0.0% | 17.3% |
| 1.0 | 5 | 2 | 0.0% | 15.0% |
| 1.0 | 5 | 3 | 0.0% | 13.8% |
| 1.0 | 10 | 1 | 1.4% | 61.4% |
| 1.0 | 10 | 2 | 1.7% | 58.6% |
| 1.0 | 10 | 3 | 1.5% | 52.7% |
| 1.2 | 5 | 1 | 0.0% | 3.2% |
| 1.2 | 5 | 2 | 0.0% | 2.6% |
| 1.2 | 5 | 3 | 0.0% | 2.1% |
| 1.2 | 10 | 1 | 0.1% | 32.8% |
| 1.2 | 10 | 2 | 0.0% | 29.5% |
| 1.2 | 10 | 3 | 0.1% | 27.6% |

## Lectura

- **P(ruina)**: probabilidad de que el capital caiga por debajo del margen
  de esa fila (ya no se puede abrir ni una operación más) en algún punto de
  un ensayo de 100 operaciones.
- **P(drawdown > 30%)**: probabilidad de que el capital caiga más del 30%
  desde su máximo histórico en algún punto del mismo ensayo.
- **Excluidas por tope** / **Incluidas**: cuántas operaciones del pool de
  esa fila el tope de SL de esa fila excluye por completo (riesgo
  planeado al abrir mayor al tope) vs. cuántas sobreviven para remuestrear
  — el tope de SL ya NO trunca pérdidas, excluye operaciones enteras (ver
  `app/backtesting/risk_analysis.py`).
- **Bloque**: tamaño del bloque de operaciones consecutivas remuestreadas
  juntas (1 = i.i.d., el default; mayor a 1 = preserva algo de la
  correlación temporal real entre pérdidas consecutivas, que el i.i.d.
  subestima).

**Advertencia importante sobre la columna "Máx. posiciones simultáneas"**:
esta tabla NO es fiable para decidir cuántas posiciones simultáneas
permitir en vivo. Dos sesgos van en la misma dirección equivocada: (1) el
drawdown se mide solo al cierre de cada lote de posiciones, nunca dentro
de él -- con lotes más grandes hay menos puntos de medición en el mismo
ensayo, lo que sesga el drawdown medido hacia abajo; (2) el método trata
cada operación remuestreada como independiente, pero las altcoins del
universo están correlacionadas entre sí -- "3 posiciones simultáneas" en
la práctica se parece más a una apuesta direccional grande que a 3
apuestas independientes, algo que este bootstrap no puede capturar. El
resultado neto es que más posiciones simultáneas aparenta menos riesgo en
esta tabla -- al revés de la realidad. No usar esta columna para justificar
`MAX_SIMULTANEOUS_POSITIONS`; el riesgo de correlación entre símbolos se
acota aparte con `MAX_SAME_DIRECTION_POSITIONS` (ver `docs/FASE3_PLAN.md`
sección 4). Ver también `app/backtesting/risk_analysis.py` (limitación 2
del docstring del módulo).
