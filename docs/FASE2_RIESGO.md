# Fase 2 — Análisis de riesgo de ruina (bootstrap)

> Generado por `scripts/analyze_risk.py`. Ver `app/backtesting/risk_analysis.py`
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

**ema_cross_9_21** -- 243 operaciones OOS, PF 1.12, esperanza por operación +0.40 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 243 | 0 | n/a (0 de 243 elegibles) | n/a |
| 5 | 1 | 50% | 1 | 0 | 243 | 0.1% | 34.0% |
| 5 | 1 | sin tope | 1 | 0 | 243 | 0.1% | 37.1% |
| 5 | 2 | 40% | 1 | 243 | 0 | n/a (0 de 243 elegibles) | n/a |
| 5 | 2 | 50% | 1 | 0 | 243 | 0.1% | 32.6% |
| 5 | 2 | sin tope | 1 | 0 | 243 | 0.2% | 31.9% |
| 5 | 3 | 40% | 1 | 243 | 0 | n/a (0 de 243 elegibles) | n/a |
| 5 | 3 | 50% | 1 | 0 | 243 | 0.1% | 29.2% |
| 5 | 3 | sin tope | 1 | 0 | 243 | 0.1% | 28.9% |
| 10 | 1 | 40% | 1 | 243 | 0 | n/a (0 de 243 elegibles) | n/a |
| 10 | 1 | 50% | 1 | 0 | 243 | 8.6% | 77.0% |
| 10 | 1 | sin tope | 1 | 0 | 243 | 8.0% | 77.3% |
| 10 | 2 | 40% | 1 | 243 | 0 | n/a (0 de 243 elegibles) | n/a |
| 10 | 2 | 50% | 1 | 0 | 243 | 7.8% | 71.7% |
| 10 | 2 | sin tope | 1 | 0 | 243 | 7.1% | 73.5% |
| 10 | 3 | 40% | 1 | 243 | 0 | n/a (0 de 243 elegibles) | n/a |
| 10 | 3 | 50% | 1 | 0 | 243 | 6.0% | 66.6% |
| 10 | 3 | sin tope | 1 | 0 | 243 | 6.5% | 67.2% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 243 | 0.1% | 29.3% |
| 5 | 3 | 50% | 5 | 0 | 243 | 0.2% | 37.6% |

### `trend_atr_stop_9_21_50`

**trend_atr_stop_9_21_50** -- 496 operaciones OOS, PF 1.02, esperanza por operación +0.03 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 164 | 332 | 0.0% | 1.9% |
| 5 | 1 | 50% | 1 | 0 | 496 | 0.0% | 8.6% |
| 5 | 1 | sin tope | 1 | 0 | 496 | 0.0% | 8.0% |
| 5 | 2 | 40% | 1 | 164 | 332 | 0.0% | 2.1% |
| 5 | 2 | 50% | 1 | 0 | 496 | 0.0% | 7.8% |
| 5 | 2 | sin tope | 1 | 0 | 496 | 0.0% | 8.1% |
| 5 | 3 | 40% | 1 | 164 | 332 | 0.0% | 2.2% |
| 5 | 3 | 50% | 1 | 0 | 496 | 0.0% | 7.5% |
| 5 | 3 | sin tope | 1 | 0 | 496 | 0.0% | 6.3% |
| 10 | 1 | 40% | 1 | 164 | 332 | 0.0% | 28.3% |
| 10 | 1 | 50% | 1 | 0 | 496 | 0.4% | 50.4% |
| 10 | 1 | sin tope | 1 | 0 | 496 | 0.4% | 50.2% |
| 10 | 2 | 40% | 1 | 164 | 332 | 0.1% | 26.9% |
| 10 | 2 | 50% | 1 | 0 | 496 | 0.2% | 47.4% |
| 10 | 2 | sin tope | 1 | 0 | 496 | 0.4% | 45.8% |
| 10 | 3 | 40% | 1 | 164 | 332 | 0.0% | 26.8% |
| 10 | 3 | 50% | 1 | 0 | 496 | 0.4% | 45.8% |
| 10 | 3 | sin tope | 1 | 0 | 496 | 0.4% | 44.9% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 496 | 0.0% | 7.6% |
| 5 | 3 | 50% | 5 | 0 | 496 | 0.0% | 15.9% |

### `mean_reversion_rsi14_bb20`

**mean_reversion_rsi14_bb20** -- 2738 operaciones OOS, PF 0.74, esperanza por operación -0.38 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 129 | 2609 | 0.0% | 28.2% |
| 5 | 1 | 50% | 1 | 0 | 2738 | 0.0% | 35.4% |
| 5 | 1 | sin tope | 1 | 0 | 2738 | 0.0% | 36.2% |
| 5 | 2 | 40% | 1 | 129 | 2609 | 0.0% | 27.0% |
| 5 | 2 | 50% | 1 | 0 | 2738 | 0.0% | 32.0% |
| 5 | 2 | sin tope | 1 | 0 | 2738 | 0.0% | 32.6% |
| 5 | 3 | 40% | 1 | 129 | 2609 | 0.0% | 27.3% |
| 5 | 3 | 50% | 1 | 0 | 2738 | 0.0% | 31.8% |
| 5 | 3 | sin tope | 1 | 0 | 2738 | 0.0% | 33.6% |
| 10 | 1 | 40% | 1 | 129 | 2609 | 2.4% | 80.7% |
| 10 | 1 | 50% | 1 | 0 | 2738 | 4.0% | 82.8% |
| 10 | 1 | sin tope | 1 | 0 | 2738 | 3.3% | 83.0% |
| 10 | 2 | 40% | 1 | 129 | 2609 | 2.5% | 80.4% |
| 10 | 2 | 50% | 1 | 0 | 2738 | 4.5% | 83.0% |
| 10 | 2 | sin tope | 1 | 0 | 2738 | 4.9% | 82.8% |
| 10 | 3 | 40% | 1 | 129 | 2609 | 2.4% | 78.5% |
| 10 | 3 | 50% | 1 | 0 | 2738 | 4.0% | 79.5% |
| 10 | 3 | sin tope | 1 | 0 | 2738 | 3.9% | 80.7% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 2738 | 0.0% | 30.9% |
| 5 | 3 | 50% | 5 | 0 | 2738 | 0.0% | 45.2% |

### `donchian_breakout_20`

**donchian_breakout_20** -- 749 operaciones OOS, PF 0.90, esperanza por operación -0.25 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 226 | 523 | 0.0% | 34.1% |
| 5 | 1 | 50% | 1 | 0 | 749 | 0.3% | 56.8% |
| 5 | 1 | sin tope | 1 | 0 | 749 | 0.0% | 57.2% |
| 5 | 2 | 40% | 1 | 226 | 523 | 0.0% | 33.7% |
| 5 | 2 | 50% | 1 | 0 | 749 | 0.1% | 51.7% |
| 5 | 2 | sin tope | 1 | 0 | 749 | 0.1% | 55.2% |
| 5 | 3 | 40% | 1 | 226 | 523 | 0.0% | 30.9% |
| 5 | 3 | 50% | 1 | 0 | 749 | 0.2% | 52.0% |
| 5 | 3 | sin tope | 1 | 0 | 749 | 0.2% | 52.2% |
| 10 | 1 | 40% | 1 | 226 | 523 | 6.5% | 76.8% |
| 10 | 1 | 50% | 1 | 0 | 749 | 18.4% | 89.3% |
| 10 | 1 | sin tope | 1 | 0 | 749 | 19.7% | 90.0% |
| 10 | 2 | 40% | 1 | 226 | 523 | 7.0% | 74.0% |
| 10 | 2 | 50% | 1 | 0 | 749 | 19.1% | 88.5% |
| 10 | 2 | sin tope | 1 | 0 | 749 | 18.7% | 88.9% |
| 10 | 3 | 40% | 1 | 226 | 523 | 6.8% | 73.4% |
| 10 | 3 | 50% | 1 | 0 | 749 | 17.3% | 85.8% |
| 10 | 3 | sin tope | 1 | 0 | 749 | 19.4% | 85.7% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 749 | 0.2% | 51.0% |
| 5 | 3 | 50% | 5 | 0 | 749 | 1.1% | 65.7% |

### `funding_contrarian_percentile_experimental`

**funding_contrarian_percentile_experimental (experimental)** -- 184 operaciones OOS, PF 1.17, esperanza por operación +0.55 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 184 | 0 | n/a (0 de 184 elegibles) | n/a |
| 5 | 1 | 50% | 1 | 0 | 184 | 0.0% | 29.8% |
| 5 | 1 | sin tope | 1 | 0 | 184 | 0.1% | 29.8% |
| 5 | 2 | 40% | 1 | 184 | 0 | n/a (0 de 184 elegibles) | n/a |
| 5 | 2 | 50% | 1 | 0 | 184 | 0.1% | 24.6% |
| 5 | 2 | sin tope | 1 | 0 | 184 | 0.1% | 26.1% |
| 5 | 3 | 40% | 1 | 184 | 0 | n/a (0 de 184 elegibles) | n/a |
| 5 | 3 | 50% | 1 | 0 | 184 | 0.1% | 24.9% |
| 5 | 3 | sin tope | 1 | 0 | 184 | 0.1% | 24.1% |
| 10 | 1 | 40% | 1 | 184 | 0 | n/a (0 de 184 elegibles) | n/a |
| 10 | 1 | 50% | 1 | 0 | 184 | 5.1% | 70.8% |
| 10 | 1 | sin tope | 1 | 0 | 184 | 4.8% | 71.4% |
| 10 | 2 | 40% | 1 | 184 | 0 | n/a (0 de 184 elegibles) | n/a |
| 10 | 2 | 50% | 1 | 0 | 184 | 6.3% | 65.5% |
| 10 | 2 | sin tope | 1 | 0 | 184 | 5.7% | 65.2% |
| 10 | 3 | 40% | 1 | 184 | 0 | n/a (0 de 184 elegibles) | n/a |
| 10 | 3 | 50% | 1 | 0 | 184 | 4.8% | 60.1% |
| 10 | 3 | sin tope | 1 | 0 | 184 | 4.5% | 61.4% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 184 | 0.0% | 23.9% |
| 5 | 3 | 50% | 5 | 0 | 184 | 0.1% | 25.6% |

### `funding_contrarian_experimental`

**funding_contrarian_experimental (experimental)** -- 14 operaciones OOS, PF 1.43, esperanza por operación +1.24 USDT.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 14 | 0 | n/a (0 de 14 elegibles) | n/a |
| 5 | 1 | 50% | 1 | 0 | 14 | 0.0% | 9.4% |
| 5 | 1 | sin tope | 1 | 0 | 14 | 0.0% | 8.9% |
| 5 | 2 | 40% | 1 | 14 | 0 | n/a (0 de 14 elegibles) | n/a |
| 5 | 2 | 50% | 1 | 0 | 14 | 0.0% | 7.6% |
| 5 | 2 | sin tope | 1 | 0 | 14 | 0.0% | 8.6% |
| 5 | 3 | 40% | 1 | 14 | 0 | n/a (0 de 14 elegibles) | n/a |
| 5 | 3 | 50% | 1 | 0 | 14 | 0.0% | 7.0% |
| 5 | 3 | sin tope | 1 | 0 | 14 | 0.0% | 5.7% |
| 10 | 1 | 40% | 1 | 14 | 0 | n/a (0 de 14 elegibles) | n/a |
| 10 | 1 | 50% | 1 | 0 | 14 | 0.7% | 43.4% |
| 10 | 1 | sin tope | 1 | 0 | 14 | 0.9% | 42.7% |
| 10 | 2 | 40% | 1 | 14 | 0 | n/a (0 de 14 elegibles) | n/a |
| 10 | 2 | 50% | 1 | 0 | 14 | 1.0% | 35.1% |
| 10 | 2 | sin tope | 1 | 0 | 14 | 0.6% | 39.1% |
| 10 | 3 | 40% | 1 | 14 | 0 | n/a (0 de 14 elegibles) | n/a |
| 10 | 3 | 50% | 1 | 0 | 14 | 0.4% | 32.6% |
| 10 | 3 | sin tope | 1 | 0 | 14 | 0.8% | 31.7% |

**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: margen 5 USDT, 3 posiciones, tope SL 50% -- bloque de 5 operaciones consecutivas vs. i.i.d.; ver limitación 4 del docstring de `app/backtesting/risk_analysis.py`):

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 50% | 1 | 0 | 14 | 0.0% | 6.4% |
| 5 | 3 | 50% | 5 | 0 | 14 | 0.0% | 0.0% |


## Pool combinado (solo referencia — escenario con esperanza negativa)

**pool combinado (ema_cross_9_21, trend_atr_stop_9_21_50, mean_reversion_rsi14_bb20, donchian_breakout_20)** -- 4226 operaciones OOS, PF 0.85, esperanza por operación -0.26 USDT.

Combina las 4 estrategias no
experimentales en una sola distribución empírica — **no** representa a
ninguna estrategia real por sí sola, es una referencia de "qué tan mal
podría ir" si el mercado se parece al conjunto observado. Las tablas por
estrategia de arriba son las que informan decisiones específicas por
estrategia.

| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) | Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|---|---|---|
| 5 | 1 | 40% | 1 | 762 | 3464 | 0.0% | 26.8% |
| 5 | 1 | 50% | 1 | 0 | 4226 | 0.0% | 38.6% |
| 5 | 1 | sin tope | 1 | 0 | 4226 | 0.0% | 35.1% |
| 5 | 2 | 40% | 1 | 762 | 3464 | 0.0% | 24.3% |
| 5 | 2 | 50% | 1 | 0 | 4226 | 0.0% | 34.9% |
| 5 | 2 | sin tope | 1 | 0 | 4226 | 0.0% | 36.0% |
| 5 | 3 | 40% | 1 | 762 | 3464 | 0.0% | 25.8% |
| 5 | 3 | 50% | 1 | 0 | 4226 | 0.0% | 32.8% |
| 5 | 3 | sin tope | 1 | 0 | 4226 | 0.0% | 34.4% |
| 10 | 1 | 40% | 1 | 762 | 3464 | 2.5% | 77.0% |
| 10 | 1 | 50% | 1 | 0 | 4226 | 8.3% | 81.7% |
| 10 | 1 | sin tope | 1 | 0 | 4226 | 7.2% | 82.3% |
| 10 | 2 | 40% | 1 | 762 | 3464 | 2.4% | 73.6% |
| 10 | 2 | 50% | 1 | 0 | 4226 | 6.5% | 78.2% |
| 10 | 2 | sin tope | 1 | 0 | 4226 | 6.7% | 78.0% |
| 10 | 3 | 40% | 1 | 762 | 3464 | 2.6% | 71.2% |
| 10 | 3 | 50% | 1 | 0 | 4226 | 6.8% | 76.5% |
| 10 | 3 | sin tope | 1 | 0 | 4226 | 6.8% | 77.7% |

## Sensibilidad: aislando la deriva del efecto de margen/posiciones

Mismo pool combinado, filtrado primero al tope de SL del
50% (0
operaciones excluidas de 4226), con las pérdidas
reescaladas para alcanzar exactamente el profit factor objetivo de cada
fila (las ganancias no se tocan) — separa cuánto del riesgo de ruina viene
de la deriva negativa de las estrategias evaluadas de cuánto viene,
estructuralmente, del margen y las posiciones simultáneas elegidas, para
cualquier estrategia con un perfil de resultados parecido:

| PF objetivo | Margen (USDT) | Máx. posiciones simultáneas | P(ruina) | P(drawdown > 30%) |
|---|---|---|---|---|
| 1.0 | 5 | 1 | 0.0% | 13.2% |
| 1.0 | 5 | 2 | 0.0% | 11.5% |
| 1.0 | 5 | 3 | 0.0% | 9.7% |
| 1.0 | 10 | 1 | 1.3% | 58.1% |
| 1.0 | 10 | 2 | 0.9% | 53.7% |
| 1.0 | 10 | 3 | 1.0% | 53.1% |
| 1.2 | 5 | 1 | 0.0% | 2.0% |
| 1.2 | 5 | 2 | 0.0% | 1.7% |
| 1.2 | 5 | 3 | 0.0% | 1.6% |
| 1.2 | 10 | 1 | 0.1% | 28.8% |
| 1.2 | 10 | 2 | 0.1% | 25.0% |
| 1.2 | 10 | 3 | 0.1% | 23.0% |

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
