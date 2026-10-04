# Reejecucion del backtest de Fase 2 con el redondeo corregido (v1 -> v2)

Estado: **v2 ejecutada y verificada**. El control de validez se resolvio: la
diferencia en las estrategias sin error de redondeo se explica por la ventana del
backtest, no por el redondeo ni por la cache (seccion 4). Las conclusiones de Fase 2
quedan en la seccion 5. La propuesta de benchmark de azar esta en la seccion 6 (no
implementada).

Sin cambiar criterios de descarte (`docs/FASE2_CRITERIOS.md`), parametros
congelados (`app/config.py`, `BACKTEST_*`, `MAX_SL_MARGIN_LOSS_PCT=50`), lista de
estrategias elegibles ni datos de origen. Lo unico que cambia entre v1 y v2 es la
funcion que compara el tope de SL (y, como se vio en la seccion 4, la fecha de fin
de la ventana, que v1 no tenia fijada).

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

Lo mismo ocurria en el motor en vivo (`app/trading/sl_calc.py`, compartido). Se
corrigio redondeando el porcentaje a 9 decimales en `margin_loss_pct`, con un test
que fija el borde en 50.0 para varios precios.

## 2. Descartes por estrategia: antes (v1) y ahora (v2)

Columnas: descartes por tope de SL, de ellos los que eran solo error de coma
flotante, y operaciones IS/OOS registradas. Fuente: `data/backups/minerva_fase2_v1.db`
(v1) y `data/backups/minerva_fase2_v2.db` (v2).

| estrategia | descartes v1 | de ellos por coma flotante | descartes v2 | IS v1 | OOS v1 | IS v2 | OOS v2 |
|---|---|---|---|---|---|---|---|
| ema_cross_9_21 | 2042 | **2042** | 0 | 416 | 243 | 1018 | 556 |
| trend_atr_stop_9_21_50 | 1380 | 0 | 1380 | 782 | 496 | 782 | 493 |
| mean_reversion_rsi14_bb20 | 529 | 0 | 529 | 3256 | 2738 | 3255 | 2737 |
| donchian_breakout_20 | 3329 | 0 | 3329 | 1164 | 749 | 1164 | 749 |
| funding_contrarian_experimental | 509 | **509** | 0 | 140 | 14 | 238 | 21 |
| funding_contrarian_percentile_experimental | 1037 | **1037** | 0 | 179 | 184 | 275 | 290 |

Los 3.588 descartes por coma flotante de v1 entran en v2. Los descartes de trend,
mean reversion y donchian no cambian: son descartes reales por encima del tope.

## 3. Comandos ejecutados y nota sobre la v1

La v2 se corrio sobre la copia `data/backups/minerva_fase2_v2.db`, porque
`run_backtest.py` borra las tablas `backtest_*` antes de correr
(`backtest_repo.clear_results`). La base de trabajo `data/minerva.db` no se toco.

```powershell
$env:DATABASE_PATH = "data/backups/minerva_fase2_v2.db"
python scripts/run_backtest.py
python scripts/analyze_risk.py
Remove-Item Env:DATABASE_PATH
python scripts/compare_fase2_runs.py `
    --v1 data/backups/minerva_fase2_v1.db `
    --v2 data/backups/minerva_fase2_v2.db `
    --out docs/FASE2_REEJECUCION_TABLAS.md
```

**Nota importante sobre la v1.** La corrida v1 (`run_at` 2026-10-01 23:05 UTC) se
hizo con el codigo del commit `da098a8`, donde el fin de la ventana era la hora de
ejecucion (`end_ms = int(time.time() * 1000)`). El fin fijo `BACKTEST_OFFICIAL_END_DATE`
(2026-10-01 00:00 UTC) se introdujo en el commit `1e15257`, 23 minutos despues de la
corrida v1. Por eso la v1 **no es reproducible** por si sola: depende de la hora a
la que se ejecuto. Esto explica el control de validez de la seccion 4.

Los resultados v1 quedan archivados como **corrida con el error y ventana sin fijar**.
No se usan como evidencia de Fase 2.

## 4. Control de validez: causa de las diferencias en las estrategias de control

Las tres estrategias sin descartes por coma flotante (`trend_atr_stop_9_21_50`,
`mean_reversion_rsi14_bb20`, `donchian_breakout_20`) deberian reproducir la v1
exactamente. No lo hacen, y la causa es la **ventana**, no el redondeo ni la cache.

**Cache: identica.** Las dos copias tienen la misma huella en la ventana oficial
(2022-01-01 a 2026-10-01 00:00 UTC): 60 series de velas (recuento, primer y ultimo
`open_time` y suma de verificacion de `open`/`close`) y 10 series de funding (recuento,
rango y suma de verificacion de la tasa), sin una sola diferencia. Ademas, las
especificaciones de contrato (`contract_specs_cache`) y el universo (`asset_universe`)
tienen la misma marca de tiempo en ambas (2026-10-01 23:04), anterior a la corrida v1.
Ningun dato de origen cambio entre corridas.

**Costos y slippage: sin cambios en el motor.** La extraccion del slippage en la
subfase 3.4 usa la misma aritmetica que el original (`notional * bps/10000`,
`qty * precio * bps/10000`). Los descartes de las tres estrategias son identicos en
v1 y v2 (1380, 529 y 3329), lo que confirma que el tope de SL no cambio para ellas.

**Diferencias de operaciones: 14 filas, las 14 explicadas.** Se comparo operacion por
operacion (clave: estrategia, simbolo, timeframe, segmento, entrada):

| estrategia | filas distintas | salida tras el fin oficial | entrada tras el fin oficial (solo v1) | frontera IS/OOS movida | sin explicar |
|---|---|---|---|---|---|
| trend_atr_stop_9_21_50 | 5 | 2 | 3 | 0 | 0 |
| mean_reversion_rsi14_bb20 | 6 | 3 | 2 | 1 | 0 |
| donchian_breakout_20 | 4 | 4 | 0 | 0 | 0 |

- **Salida tras el fin**: en v1 la ventana llegaba hasta las 23:05 UTC, asi que 18
  posiciones seguian abiertas despues de las 00:00 y se cerraban mas tarde (16:00 o
  22:00 UTC); en v2 todas cierran en el fin fijo (`END_OF_DATA` a las 00:00). Por eso
  cambian el precio de salida, el funding acumulado y el PnL de esas posiciones.
- **Entrada tras el fin (solo v1)**: las senales entre las 00:00 y las 23:05 del
  1 de octubre entraban en v1 y no en v2. Son 3 operaciones de trend y 2 de mean
  reversion.
- **Frontera IS/OOS movida**: el corte se calcula como `inicio + 0,7 * (fin - inicio)`.
  Al cambiar el fin, el corte se movio unas 16 horas (de 2025-04-28 19:12 a 2025-04-29 11:21 UTC), y una operacion de mean
  reversion en HYPEUSDT cambio de segmento.

**Conclusion del control:** la diferencia no proviene de cambios de codigo que afecten
al calculo de operaciones, ni de los datos. Proviene de la ventana: v1 usaba la hora de
ejecucion como fin y v2 usa el fin fijo. El control de validez se da por resuelto con
esta explicacion. Para v2 esto significa que las estrategias sin error de redondeo
**no son directamente comparables con v1** si se comparan operacion por operacion; sí
son comparables con v2 como referencia.

## 5. Resultados v1 vs v2 y conclusiones de Fase 2

### 5.1 Veredictos y simulacion de cartera

Tabla generada con `scripts/compare_fase2_runs.py` (PF sobre la cartera completa;
las columnas de cartera son medianas de 200 corridas Monte Carlo). PF IS y cartera
solo-OOS son de la base v2.

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

PF IS y Monte Carlo solo-OOS (solo v2):

| estrategia | PF IS | ops IS | PF OOS | ops OOS | cartera OOS mediana (p10–p90) |
|---|---|---|---|---|---|
| ema_cross_9_21 | 1.024 | 1018 | 0.970 | 556 | 180.8 (134.3–224.9) |
| trend_atr_stop_9_21_50 | 0.933 | 782 | 1.025 | 493 | 126.8 (114.6–140.5) |
| mean_reversion_rsi14_bb20 | 0.713 | 3255 | 0.742 | 2737 | 9.1 (7.5–9.8) |
| donchian_breakout_20 | 1.112 | 1164 | 0.902 | 749 | 78.8 (15.8–129.0) |
| funding_contrarian_experimental | 1.028 | 238 | 1.795 | 21 | 143.7 (sin dispersion: 21 operaciones) |
| funding_contrarian_percentile_experimental | 0.904 | 275 | 1.081 | 290 | 163.4 (130.2–196.2) |

### 5.2 Conclusiones

1. **ema_cross_9_21** pasa de PF OOS 1,12 (v1, submuestra sesgada por el redondeo)
   a **0,97 con 556 operaciones OOS**. Falla los 5 criterios que se le evaluaron:
   PF OOS < 1,2; PF > 1 en el 40% de los simbolos (< 50%); positivo en el 46% de los
   folds (< 50%); PF estresado 0,91 (<= 1); drawdown OOS 186% (> 50%). **La v1 no es
   evidencia a favor**: era una submuestra donde solo entraban las operaciones cuyo
   SL no caia en el borde por casualidad de precio.

2. **funding_contrarian_experimental** tiene PF IS 1,03 sobre 238 operaciones. Su PF
   OOS de 1,80 sale de solo **21 operaciones**, asi que no tiene peso estadistico. Es
   experimental: el veredicto no la evalua (`report.py`, `_evaluate_discard_criteria`
   devuelve sin motivos para experimentales), por lo que `discarded=0` **no significa
   que apruebe**. Si se le aplicara el criterio de concentracion (<= 40%), fallaria:
   su concentracion es 76%.

3. **funding_contrarian_percentile_experimental** cambia de PF IS 0,90 (275
   operaciones) a PF OOS 1,08 (290 operaciones). Tampoco se evalua por el veredicto
   (experimental) y su concentracion es 78%, por encima del 40%.

4. **Ninguna estrategia por reglas supera los criterios congelados.** Las cuatro
   evaluadas (ema, trend, mean reversion, donchian) estan descartadas. Las dos
   experimentales no se evaluan por diseño y, si se les aplicara el criterio de
   concentracion, tampoco lo pasan.

5. **El capital final de la cartera es muy sensible al orden y a la seleccion, y no
   es evidencia.** Ejemplo: ema_cross tiene PF OOS 0,97, pero su cartera solo-OOS tiene
   mediana 181 y su cartera de periodo completo mediana 7,9. La misma estrategia, con
   las mismas operaciones, da resultados opuestos segun como se ordenen y se
   seleccionen. **La medida de referencia es el PF sobre todas las operaciones**, no
   el capital final de una simulacion de cartera.

### 5.3 Ritmo de operaciones OOS

Calculado sobre la ventana OOS real de las estrategias elegibles para cuenta real
(entradas del 2025-04-28 al 2026-09-29, 520,17 dias):

- **Elegibles (ema_cross 556, funding original 21, percentil 290): 867 operaciones
  OOS, ritmo 1,667 por dia.** La estimacion previa de 0,85 por dia (441 operaciones
  OOS) queda superada por la v2.
- **Dias hasta 100 operaciones cerradas en cuenta real, a 1,667 por dia:**
  - con aprobacion del 100% (todas las senales elegibles se ejecutan): **60,0 dias**;
  - con aprobacion del 50%: **120,0 dias**.
  Son cotas optimistas: no descuentan los rechazos del motor de riesgo (cupos,
  circuit breaker, drawdown ni tope de SL en vivo).
- **Brazo sombra con las seis estrategias:** 4846 operaciones OOS en 520,17 dias,
  **9,32 operaciones simuladas por dia**. No son señales: las señales que llegan mientras
  la misma celda tiene una posicion abierta no se cuentan (igual que el backtest), y la
  agrupacion entre estrategias de la subfase 3.5 las reduce. El numero real de senales
  por dia se medira con la tabla `signals` y `shadow_trades` en vivo.

### 5.4 Regla de decision sobre la IA (fijada ahora, antes de tener datos del LLM)

Si la diferencia de esperanza por operacion entre APROBADA y RECHAZADA **no tiene un
intervalo de confianza del 95% que excluya el cero con N >= 100 por lado**, la
conclusion es **"la IA no aporta valor"** y se detiene el gasto en la API.

Coste de evidencia: con 9,32 operaciones sombra por dia y una tasa de aprobacion
del LLM de p, hacen falta 100/p señales para 100 aprobadas, y 100/(1-p) para 100
rechazadas. Con p = 0,5 son 200 señales (**unos 21 dias**); con p = 0,25, unas 400
señales (**unos 43 dias**). Estas cifras son antes de agrupar y antes de contar las
señales que llegan con posicion abierta, asi que son cotas bajas del tiempo real.

### 5.5 Constancia

- **Ninguna estrategia de reglas tiene una ventaja demostrable** con la evidencia
  actual: ninguna supera los criterios congelados, y la unica con PF OOS por encima de
  1,2 (funding original) tiene 21 operaciones.
- **No se justifica dinero real con la evidencia actual.** La v2 solo confirma que
  las estrategias de reglas no superan los criterios; el LLM no esta validado (su
  medicion empieza con la subfase 3.6).

## 6. Propuesta: benchmark de entradas aleatorias (no implementado)

**Pregunta:** ¿alguna estrategia se distingue del azar con las mismas salidas, costos,
simbolos y frecuencia?

**Diseno propuesto**, por cada estrategia y cada celda (simbolo x timeframe x
segmento):

- **Mismo numero de entradas** que la estrategia en esa celda, en las mismas barras de
  ventana (sorteadas uniformemente entre las barras elegibles de la celda).
- **Misma proporcion LONG/SHORT** que la estrategia en esa celda.
- **Mismas salidas:** SL, TP y trailing se calculan con la misma funcion de la
  estrategia en la barra de entrada (`stop_price`, `take_profit_price`,
  `trailing_distance`, o el respaldo porcentual si no existen). Asi el riesgo
  estructural es identico y solo cambia la senal de entrada.
- **Mismos costos:** fees, slippage y funding, con las mismas funciones del backtest.
- **Mismo motor:** `run_backtest` sin cambios, con las entradas sustituidas.
- **Semillas:** N corridas con semillas distintas (propuesta: N = 100), y se guarda
  la distribucion de PF OOS y de esperanza por operacion.

**Comparacion:** para cada estrategia, el p-valor es la fraccion de semillas aleatorias
con PF OOS >= al PF OOS de la estrategia. Como son 6 estrategias, se corrige por
comparaciones multiples (Bonferroni: umbral 0,05/6 ≈ 0,008). Se reporta tambien la
esperanza por operacion, que no depende del tamaño de la muestra de la misma forma.

**Costo en tiempo:** una corrida completa de `run_backtest.py` tarda unos **5 minutos**
(medido en `backtest_runs`: 18:00:01 a 18:04:54 UTC en la v2). Cada semilla es una
corrida completa de las seis estrategias, asi que:

- 100 semillas en un solo nucleo: unas **8 horas**.
- Paralelizando por estrategia (6 procesos) y con nucleos suficientes: unas **1,5 horas**.
- Almacenamiento: cada corrida escribe sus operaciones; las semillas deben guardarse
  en bases separadas (no en la v2), o solo sus agregados (PF, esperanza, n).

**Recomendacion:** no implementarlo antes de tener la regla de decision de la seccion
5.4 en marcha. Si ninguna estrategia se distingue del azar, la conclusion de la
seccion 5 se refuerza; si alguna se distingue, habria que revisar primero si el
resultado sobrevive a los costos de estres (PF estresado).
