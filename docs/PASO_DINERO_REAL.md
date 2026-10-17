# Reporte de paso a dinero real (subfase 3.8)

**Solo recomienda. v1 es 100 % paper trading.** El reporte nunca habilita ejecución real:
`BitunixBackend` sigue desactivado, ni el módulo ni el script lo importan (hay tests
estructurales que lo verifican) y pasar a dinero real es una decisión manual tuya que
requeriría una fase nueva.

- Lógica: `app/trading/real_money_report.py`. CLI: `scripts/real_money_report.py`.
- Mismo patrón que `scripts/ai_value_report.py`: solo lectura (`mode=ro`) sobre una **copia** de
  la base, con un `assert` de que la ruta no es `settings.database_path`.

## Cómo se usa

```
python scripts/real_money_report.py --db <copia consistente de la base>
python scripts/real_money_report.py --db <copia> --log-file <log del bot> --out docs/PASO_DINERO_REAL_<fecha>.txt
```

La copia se hace igual que para el veredicto (`docs/VEREDICTO_3_6.md`): copia consistente, nunca la
base de trabajo. `--log-file` es opcional y cuenta las líneas CRITICAL "Reconciliacion fallida",
que el bot solo escribe en el log (no en la base).

| Código | Significado |
|---|---|
| 0 | Informe impreso: conclusión negativa o evaluación completa |
| 2 | Error de uso (p. ej. `--out` con el nombre del registro oficial, `--log-file` inexistente) |
| 3 | Reveal no permitido: solo se imprime **qué falta** y los contadores de la medición |
| 4 | Bandera de pruebas rechazada (la base no es sintética) |
| 5 | `--out` rechazado porque el archivo ya existe (nunca se sobrescribe) |

`--out` solo guarda informes con conclusión (nunca el de "falta algo") y **no puede llamarse como el
registro oficial** (`VEREDICTO_3_6_REGISTRO*.txt`): este reporte no fabrica ese registro. Usa otro
nombre, p. ej. `docs/PASO_DINERO_REAL_<fecha>.txt`.

## Orden estricto (qué se lee y cuándo)

1. **Guardia.** Se calcula el estado de la medición y `is_outcome_reveal_allowed`: gate abierto
   (300 conglomerados efectivos por lado) **y** registro oficial del veredicto. Si es falso, solo se
   imprime una lista de lo que falta; **no se consulta `trades` ni ningún resultado** (un test
   parchea los cargadores para que fallen si se llaman).
2. **Veredicto como insumo.** Se lee el registro oficial. Si no dice `APORTA_VALOR` (es
   `INCONCLUSO` o `NO_APORTA_VALOR`), el informe concluye **"no pasar a dinero real"** y termina:
   no se evalúa nada más. El veredicto se **cita** del registro (incluida la marca `MAGNITUD_BAJA`),
   no se recalcula.
3. **Evaluación.** Solo con `APORTA_VALOR`: criterios del brazo ejecutado, riesgos operativos
   medidos y la transferencia a las estrategias elegibles.

## Criterios y reconciliación con la fila 3.8 original

Cada criterio devuelve `PASA`, `NO PASA` o `SIN DATOS`. **La falta de datos nunca cuenta como
"pasa".** La recomendación "pasar a evaluación manual" solo sale si **todos** pasan.

| Criterio | Regla | Cambio frente a la fila 3.8 original |
|---|---|---|
| Valor de la IA | Veredicto oficial = `APORTA_VALOR` | **Se retiran los regímenes <30 / 30-100 / ≥100 por lado** y la "evidencia baja". La 3.6 fija un único punto de análisis (300 conglomerados efectivos por lado, δ = 3 %, tres veredictos); mostrar cifras antes contradice la regla de una sola mirada, y con N = 100 por lado solo se detectaría ~12-18 % del margen (seis veces δ) |
| Días de paper trading | ≥ `MIN_PAPER_TRADING_DAYS` (30), contados desde `system_state.real_arm_started_at` | Se define el inicio (lo fija la 3.9); sin esa clave se usa la primera operación y se avisa |
| Operaciones cerradas | ≥ `MIN_CLOSED_TRADES` (100) en el brazo ejecutado | Se mantiene, y se muestran los **conglomerados efectivos** (operaciones solapadas del mismo símbolo y dirección cuentan como una) |
| Rentabilidad | PF > `MIN_PROFIT_FACTOR` (1,3) **y** límite inferior del IC 95 % del PF > 1,0 (bootstrap por conglomerados, mismas constantes que `ai_value.py`) | Se añade el IC: con 100 operaciones el PF puntual es ruido (decisión tuya del 2026-10-17). Con < 100 operaciones o sin pérdidas es `SIN DATOS` |
| Drawdown | `drawdown_stop_would_have_triggered_count` menos la línea base `real_arm_drawdown_baseline` ≤ 1 | El contador es acumulado: se necesita la línea base (la fija la 3.9); sin ella, `SIN DATOS` |
| Concentración | Una operación o símbolo ≤ `BACKTEST_CONCENTRATION_LIMIT_PCT` (40 %) del PnL neto | Se reutiliza `concentration_pct` tal cual. PnL neto total ≤ 0 ⇒ `NO PASA` |
| Reconciliación | Cero operaciones con `fill_source='TICK_UNRECONCILED'`, `close_reason='RECONCILE_FAILED'` o `reconciliation_failed=1`, más (con `--log-file`) cero líneas CRITICAL | Los CRITICAL solo existen en el log; la base ofrece estos sustitutos. Sin `--log-file` el informe lo dice |
| Ritmo "60 / 120 días a 100 operaciones" | Reemplazado por el ritmo vivo observado cuando exista el brazo | Eran cotas del backtest |

Las operaciones cerradas sin PnL (p. ej. `RECONCILE_FAILED`) no entran a ninguna métrica de
rentabilidad, pero sí marcan el criterio de reconciliación.

La sección **"Transferencia a las estrategias elegibles"** es **exploratoria y no es criterio**: el
veredicto 3.6 mezcla todas las estrategias (`mean_reversion` ≈ 58 % de las operaciones), pero la
cuenta solo operaría `ema_cross_9_21` y las dos de funding (`REAL_ACCOUNT_ELIGIBLE_STRATEGIES`). Se
muestra la diferencia de esperanza de las elegibles con su N, solo como contexto.

## Dependencia: subfase 3.9 (brazo ejecutado)

El brazo ejecutado (la cuenta paper operando solo las `APROBADA` a tiempo, a través del motor de
riesgo) lo implementa la **subfase 3.9** (`docs/FASE3_9_BRAZO_EJECUTADO.md`). Mientras no esté
desplegado y no haya abierto y cerrado operaciones, la tabla `trades` está vacía y casi todos los
criterios salen `SIN DATOS`. La 3.9:

- aplica `real_open_allowed` y marca las operaciones con `decision_source='LLM_APROBADA'`;
- guarda cada decisión (abierta u omitida, con su motivo) en `paper_execution_log`, vinculada por
  `signal_group_key` a la sombra: de ahí salen las **omitidas por motivo** y la **entrada ejecutada vs
  sombra** (retraso y diferencia de precio en pb) que ahora muestra la sección de riesgos operativos;
- fija `system_state.real_arm_started_at` y `real_arm_drawdown_baseline` al activar el brazo.

Los resultados de esa cuenta (PnL, balance, equity, PF) están **ocultos por API y Telegram hasta el
registro oficial del veredicto**, igual que los de las sombras.

## Riesgos operativos de pasar a dinero real

Leyenda: **[V]** verificado contra documentación oficial de Bitunix el 2026-10-17 · **[D]** dato
medido en nuestra base · **[S]** supuesto del simulador · **[NV]** **no verificado**.

La sección "Riesgos operativos medidos" del informe calcula lo medible ([D]) con la copia:
retrasos de decisión, símbolos no ejecutables, huecos de disponibilidad y PF estresado. El resto no
se puede medir desde la base.

| Riesgo | Qué se sabe | Estado |
|---|---|---|
| **Latencia / retraso de decisión** | La sombra decide al cierre de la vela; la cuenta real entraría 20-75 s después. En la copia del 2026-10-17 (17 filas de la medición) 2 superan `LLM_REAL_MAX_DELAY_SECONDS` = 60 s: esas aprobadas **no** se abrirían en la cuenta real. La latencia de las órdenes de Bitunix no está documentada | [D] muestra chica · [NV] latencia de la API |
| **Slippage / precio de entrada** | La sombra entra al cierre de la vela sin slippage; la real entraría a mercado tras el retraso. `BACKTEST_SLIPPAGE_BPS` = 5 es un supuesto, no una medición. La documentación de *Place order* no menciona slippage ni protección de precio (ausencia verificada); `trading_pairs` documenta el campo `priceProtectScope` pero no su semántica. El informe estresa el PF con slippage ×2 y ×3; la comparación ejecutado vs. sombra necesita la 3.9 | [S] 5 bps · [V] ausencia en la doc · [NV] semántica de `priceProtectScope` |
| **Comisiones** | VIP 0 futuros: 0,02 % maker / 0,06 % taker (bitunix.com/service/handling-fee). La página no dice sobre qué base se cobra ni que una orden a mercado sea siempre taker. Con margen 5 × 10x, ida y vuelta taker ≈ 1,2 % del margen (≈ 40 % de δ). Tu nivel real puede diferir | [V] tabla · [NV] base de cobro y tipo de orden |
| **Funding** | La documentación del *funding rate batch* define `fundingRate`, `fundingInterval` (horas) y `nextFundingTime`; el ejemplo es 8 h pero varía por símbolo. No documenta cómo se cobra | [V] campos · [NV] mecánica de cobro |
| **Tamaño mínimo de orden** | `trading_pairs` documenta `minTradeVolume` ("Minimum opening amount (base currency)") y `basePrecision`; **no hay un mínimo en USDT documentado**. Con margen 5 × 10x = 50 USDT de nocional, en la copia del 2026-10-17 `TRXUSDT` (mínimo 150 TRX) **no se puede abrir** (50 USDT / precio, redondeado hacia abajo, da 148 TRX) y `XMRUSDT` no tiene precio en caché. Las sombras sí abren TRX: **el veredicto incluye operaciones que la cuenta real no podría abrir** | [V] campos · [D] cálculo · [NV] mínimo real en USDT |
| **Protección con el PC apagado** | Hubo un hueco de 58 h sin evaluaciones (14-17 oct, cierre abrupto). En paper nadie protege la posición; en dinero real el SL/TP debería vivir **en el exchange**: *Place order* documenta `tpPrice`/`slPrice` con `MARK_PRICE`/`LAST_PRICE`, pero no su ejecución en saltos de precio ni los fills parciales | [D] hueco · [V] parámetros · [NV] comportamiento |
| **Límites de tasa** | La página de introducción de la API no los documenta | [NV] |
| **Modo de posición** | `tradeSide` solo se exige en *hedge-mode*; se ignora qué modo tendría la cuenta | [NV] |
| **Costo del LLM** | ≈ 0,0057 USD por llamada ≈ 0,11 % del margen de 5 USDT: despreciable frente a δ | [D] |

**Lo no verificado hay que cerrarlo con la documentación oficial (o con una prueba controlada en
una fase futura) antes de pensar en dinero real**; este reporte no lo cierra.

## Qué no hace

- No envía órdenes ni habilita nada: la conclusión más positiva posible es "pasar a evaluación
  manual".
- No toca la base de trabajo, el `.env`, `data/` ni el prompt congelado.
- No calcula el veredicto de la IA ni el placebo: eso es `scripts/ai_value_report.py` y
  `scripts/placebo_check.py` (`docs/VEREDICTO_3_6.md`).
