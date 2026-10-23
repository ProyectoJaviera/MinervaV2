# Tablas de comparacion Fase 2 (v1 vs v2)

Generado: 2026-10-04T15:14:44
- v1: `data/backups/minerva_fase2_v1.db`
- v2: `data/backups/minerva_fase2_v2.db`

## Descartes por tope de SL y operaciones por segmento

| estrategia | descartes totales v2 | de ellos por coma flotante (recuperables) | IS v2 | OOS v2 |
|---|---|---|---|---|
| ema_cross_9_21 | 0 | 0 | 1018 | 556 |
| trend_atr_stop_9_21_50 | 1380 | 0 | 782 | 493 |
| mean_reversion_rsi14_bb20 | 529 | 0 | 3255 | 2737 |
| donchian_breakout_20 | 3329 | 0 | 1164 | 749 |
| funding_contrarian_experimental | 0 | 0 | 238 | 21 |
| funding_contrarian_percentile_experimental | 0 | 0 | 275 | 290 |

Para v1 (el error), los mismos descartes se leen de la base v1:

| estrategia | descartes v1 | de ellos recuperables | IS v1 | OOS v1 |
|---|---|---|---|---|
| ema_cross_9_21 | 2042 | 2042 | 416 | 243 |
| trend_atr_stop_9_21_50 | 1380 | 0 | 782 | 496 |
| mean_reversion_rsi14_bb20 | 529 | 0 | 3256 | 2738 |
| donchian_breakout_20 | 3329 | 0 | 1164 | 749 |
| funding_contrarian_experimental | 509 | 509 | 140 | 14 |
| funding_contrarian_percentile_experimental | 1037 | 1037 | 179 | 184 |

## Veredictos y simulacion de cartera (v1 vs v2)

| estrategia | corrida | PF OOS | PF estresado | ops totales | DD OOS % | cartera capital mediana | cartera DD mediana % | cartera MTM DD mediana % |
|---|---|---|---|---|---|---|---|---|
| ema_cross_9_21 | v1 | 1.123 | 1.052 | 659 | 85.0 | 253.72 | 55.9 | 55.1 |
| ema_cross_9_21 | v2 | 0.970 | 0.909 | 1574 | 185.6 | 7.92 | 95.0 | 95.0 |
| trend_atr_stop_9_21_50 | v1 | 1.023 | 0.865 | 1278 | 59.9 | 8.70 | 91.3 | 91.3 |
| trend_atr_stop_9_21_50 | v2 | 1.025 | 0.867 | 1275 | 59.9 | 8.70 | 91.3 | 91.3 |
| mean_reversion_rsi14_bb20 | v1 | 0.740 | 0.625 | 5994 | 880.4 | 9.24 | 91.6 | 91.6 |
| mean_reversion_rsi14_bb20 | v2 | 0.742 | 0.627 | 5992 | 849.5 | 9.24 | 91.6 | 91.6 |
| donchian_breakout_20 | v1 | 0.903 | 0.829 | 1913 | 163.1 | 163.36 | 93.1 | 93.0 |
| donchian_breakout_20 | v2 | 0.902 | 0.828 | 1913 | 163.1 | 162.63 | 93.1 | 93.0 |
| funding_contrarian_experimental | v1 | 1.435 | 1.342 | 154 | 15.4 | 114.65 | 51.4 | 52.6 |
| funding_contrarian_experimental | v2 | 1.795 | 1.680 | 259 | 24.9 | 108.50 | 74.0 | 73.8 |
| funding_contrarian_percentile_experimental | v1 | 1.174 | 1.100 | 363 | 38.8 | 6.42 | 93.6 | 93.6 |
| funding_contrarian_percentile_experimental | v2 | 1.081 | 1.013 | 565 | 47.8 | 145.16 | 85.4 | 79.0 |

## Ritmo de operaciones OOS de las estrategias elegibles en cuenta real

- v1: 441 operaciones OOS, ritmo 0.850 por dia
- v2: 867 operaciones OOS, ritmo 1.667 por dia
