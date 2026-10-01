# Fase 2 — Resultados finales y cierre

> Criterios y parámetros fijados ANTES de esta corrida en `docs/FASE2_CRITERIOS.md` — **no se modificaron tras ver estos resultados** (regla de pre-registro, punto 5/8 de los ajustes de Fase 2). Este documento registra el veredicto oficial, corrido con la fecha final fija `BACKTEST_OFFICIAL_END_DATE=2026-10-01` (ver punto 19 de `FASE2_CRITERIOS.md`) sobre el universo dinámico de 10 símbolos + control BTC/ETH, periodo 2022-01-01 a 2026-10-01.

## Veredicto por estrategia

| Estrategia | PF OOS agregado | Resultado |
|---|---|---|
| `ema_cross_9_21` | 1.12 | DESCARTADA (no alcanza el mínimo `BACKTEST_MIN_PROFIT_FACTOR=1.2`) |
| `trend_atr_stop_9_21_50` | 1.02 | DESCARTADA |
| `mean_reversion_rsi14_bb20` | 0.74 | DESCARTADA |
| `donchian_breakout_20` | 0.90 | DESCARTADA |
| `funding_contrarian_experimental` | — | EXPERIMENTAL, no participa en el veredicto |
| `funding_contrarian_percentile_experimental` | — | EXPERIMENTAL, no participa en el veredicto |

**Ninguna estrategia por reglas supera los criterios congelados.** Por la regla de pre-registro, esto no se relajó ni se ajustó ningún umbral para forzar un "ganador" — es exactamente el resultado honesto que `docs/FASE2_CRITERIOS.md` (punto 7, "Verificación exitosa") anticipaba como posible y aceptable: el objetivo de esta fase era que el pipeline corriera de extremo a extremo con resultados auditables, no que existiera una estrategia ganadora.

## Simulación de cartera (informativa, no participa en el veredicto)

Cuenta única: capital inicial 100 USDT, margen 10 USDT por operación, máximo 3 posiciones simultáneas, 200 corridas Monte Carlo barajando el desempate de `entry_time` (ver `FASE2_CRITERIOS.md` puntos 10 y 14).

- **`trend_atr_stop_9_21_50`**, **`mean_reversion_rsi14_bb20`** y **`funding_contrarian_percentile_experimental`**: la simulación lleva la cuenta a la ruina (capital final mediana < 10 USDT, es decir, por debajo del margen mínimo para abrir una operación más) en la mayoría de las corridas.
- **`donchian_breakout_20`**: muy inestable — rango p10–p90 de 7.2 a 330.6 USDT. El resultado depende fuertemente del orden en que caen las operaciones empatadas, no es una estrategia con un perfil de riesgo predecible.
- **`ema_cross_9_21`**: es la única que crece en **todas** las corridas Monte Carlo (capital final mediana 253.7 USDT), con un drawdown mediano del 56%. Aun así, **no cumple los criterios de descarte congelados** (PF OOS 1.12 < 1.2) — crecer en la simulación de cartera no es, por sí solo, un criterio de aprobación; los criterios de la tabla de `FASE2_CRITERIOS.md` siguen siendo los que deciden.

## Estrategias experimentales (funding)

`funding_contrarian_experimental` y `funding_contrarian_percentile_experimental` quedan marcadas como **experimentales**: no participan en el veredicto de descarte (por su historia de funding más corta y, en el caso de la v2, por exigir funding 100% real en toda la ventana de referencia — ver `FASE2_CRITERIOS.md` puntos 9, 11 y 12). Se observarán hacia adelante (en el paper trading de Fase 3, cuando corresponda) en vez de evaluarse con este mismo backtest histórico.

## Conclusión y cierre de la Fase 2

La Fase 2 se da por **cerrada**. El pipeline de backtesting (universo dinámico, motor sin sesgo de anticipación, costos/SL/TP/trailing/liquidación realistas, validación OOS + walk-forward, criterios de descarte pre-registrados, simulación de cartera informativa) corrió de punta a punta de forma auditable, que era el criterio de éxito acordado. Ninguna de las 4 estrategias por reglas evaluadas pasa a Fase 3 como estrategia candidata para dinero real ni para una ejecución automática basada únicamente en reglas fijas — esto es consistente con el diseño ya previsto en `docs/SPEC.md`, donde la decisión de trading en vivo depende de la confluencia de señales **más** un LLM (Fase 3/4), no de una sola estrategia de reglas aislada.

Ver `docs/FASE2_RIESGO.md` para el análisis de riesgo de ruina (bootstrap sobre las operaciones de este backtest) que informa los parámetros de gestión de riesgo propuestos para Fase 3, y `docs/FASE3_PLAN.md` para el plan de la siguiente fase.
