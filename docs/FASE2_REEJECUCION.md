# Reejecucion del backtest de Fase 2 con el redondeo corregido (v1 -> v2)

Estado: **v1 conservada, v2 pendiente de ejecucion**. La reejecucion la corre el
usuario: las corridas pesadas no se lanzan desde el asistente. Las tablas de la seccion 4 se generan con
`scripts/compare_fase2_runs.py` a partir de las dos bases.

Sin cambiar criterios de descarte (`docs/FASE2_CRITERIOS.md`), parametros
congelados (`app/config.py`, `BACKTEST_*`, `MAX_SL_MARGIN_LOSS_PCT=50`) ni datos
de origen. Lo unico que cambia es la funcion que compara el tope de SL.

## 1. Causa del error

El SL de respaldo de las estrategias que no definen `stop_price` (`ema_cross_9_21`,
`funding_contrarian_experimental`, `funding_contrarian_percentile_experimental`)
es `BACKTEST_FALLBACK_SL_PCT = 5%` de movimiento de precio. A 10x eso es
exactamente **50% del margen**, es decir, cae justo en el tope
`MAX_SL_MARGIN_LOSS_PCT = 50`.

La formula calculaba `(distancia / precio) * leverage * 100` en coma flotante.
Para la mayoria de los precios el resultado sale `50.000000000000014` en vez de
`50.0`, y la comparacion `sl_margin_loss_pct > max_sl_margin_loss_pct` descartaba
la entrada. En una simulacion de 20.000 precios aleatorios, el 77% quedaba por
encima del tope por ese ruido, y el descarte dependia del precio, no del riesgo.

Lo mismo ocurria en el motor en vivo (`app/trading/sl_calc.py` compartido). Se
corrigio redondeando el porcentaje a 9 decimales en `margin_loss_pct`, y se
agrego un test que fija el borde en 50.0 para varios precios.

## 2. Descartes por estrategia: antes (v1) y ahora (v2)

Lectura de la base v1 (`data/backups/minerva_fase2_v1.db`, copia tomada antes de
cualquier reejecucion). Columnas: descartes totales por tope de SL, de ellos los
que son solo error de coma flotante, operaciones IS y OOS registradas.

| estrategia | descartes v1 | de ellos por coma flotante | IS v1 | OOS v1 |
|---|---|---|---|---|
| ema_cross_9_21 | 2042 | **2042** | 416 | 243 |
| trend_atr_stop_9_21_50 | 1380 | 0 | 782 | 496 |
| mean_reversion_rsi14_bb20 | 529 | 0 | 3256 | 2738 |
| donchian_breakout_20 | 3329 | 0 | 1164 | 749 |
| funding_contrarian_experimental | 509 | **509** | 140 | 14 |
| funding_contrarian_percentile_experimental | 1037 | **1037** | 179 | 184 |

Las columnas "ahora" (v2) se completan con la salida de `compare_fase2_runs.py`
despues de la corrida. Los descartes marcados como coma flotante son los que
deberian entrar en v2; los demas (trend, mean reversion, donchian) son
descartes reales por encima del tope y no deben cambiar.

**Lo que ya se puede afirmar sin correr nada**: `ema_cross_9_21` tenia 659
operaciones registradas de 2701 posibles, `funding_contrarian_experimental` 154 de
663 y `funding_contrarian_percentile_experimental` 363 de 1400. Las tres
estrategias elegibles para cuenta real estaban muestreadas de forma sesgada por
un error de redondeo, asi que sus veredictos de Fase 2 no son fiables hasta
reejecutar.

## 3. Comandos (los ejecuta el usuario)

La base de trabajo `data/minerva.db` NO se toca: la corrida v2 se hace sobre una
copia (`data/backups/minerva_fase2_v2.db`), porque `run_backtest.py` borra las
tablas `backtest_*` antes de correr (`backtest_repo.clear_results`). La v1 queda
en `data/backups/minerva_fase2_v1.db` y el informe de riesgo v1 en
`docs/FASE2_RIESGO_v1.md`. Ambas copias ya estan creadas.

PowerShell, desde la raiz del proyecto:

```powershell
# 1) Corrida v2 sobre la copia (la variable de entorno tiene prioridad sobre .env)
$env:DATABASE_PATH = "data/backups/minerva_fase2_v2.db"
python scripts/run_backtest.py

# 2) Informe de riesgo de ruina con la v2 (escribe docs/FASE2_RIESGO.md)
python scripts/analyze_risk.py

# 3) Quitar la variable y generar las tablas comparativas (solo lectura)
Remove-Item Env:DATABASE_PATH
python scripts/compare_fase2_runs.py `
    --v1 data/backups/minerva_fase2_v1.db `
    --v2 data/backups/minerva_fase2_v2.db `
    --out docs/FASE2_REEJECUCION_TABLAS.md
```

No hace falta `download_history.py`: el rango del backtest esta fijado
(`BACKTEST_OFFICIAL_END_DATE`) y la cache ya lo cubre. Si `run_backtest.py`
reporta series faltantes, correr primero `python scripts/download_history.py`
con la misma variable `DATABASE_PATH` apuntando a la copia v2.

## 4. Control de validez de la reejecucion

Antes de dar por buena la v2, comprobar que las estrategias **sin** descartes por
coma flotante reproducen la v1 exactamente (mismo PF, mismas operaciones, mismo
Monte Carlo): `trend_atr_stop_9_21_50`, `mean_reversion_rsi14_bb20` y
`donchian_breakout_20`. Si alguna cambia, los datos de origen cambiaron entre
corridas (la cache de velas o de funding se amplio con las descargas en vivo de
la subfase 3.3), y la comparacion de las tres estrategias afectadas no es
limpia: hay que documentarlo antes de sacar conclusiones.

Tablas de la v2 (pegar la salida del paso 3 aqui):

PENDIENTE DE EJECUCION.

## 5. Ritmo de senales (docs/FASE3_PLAN.md, seccion 8)

El plan usa como ritmo de referencia **0,850 operaciones OOS por dia** de las
estrategias elegibles, calculado sobre v1 (441 operaciones OOS). Con la v2 el
numero cambia porque entran las operaciones que antes se descartaban. El ritmo
actual lo imprime `compare_fase2_runs.py` al final del reporte.

Recalculo de la seccion 8 de `docs/FASE3_PLAN.md` y de `FASE2_RIESGO.md`:
PENDIENTE DE EJECUCION (se hace despues de la corrida v2, con los conteos
reales, no con estimaciones).
