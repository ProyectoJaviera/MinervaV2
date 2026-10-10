# Integridad de velas 1m/1h/4h/1d -- incidente de estabilidad (2026-10-10)

Investigación y corrección de la Etapa 1 pedida tras el incidente de la prueba de
estabilidad (3 sombras con reconciliación fallida al reiniciar tras ~5,75 días
apagado). Diagnóstico completo del incidente en el historial de la conversación;
este documento se concentra en la causa de las velas 1m incompletas, la corrección
implementada, y el efecto cuantificado sobre los datos de la Fase 2.

## 1. Semántica de `endTime` verificada empíricamente

Antes de tocar la paginación, se verificó contra la API pública real de Bitunix
(lectura, sin credenciales, con `BitunixRestClient.get_kline` tal cual está en el
repo) qué vela queda como la "última" para distintos `endTime`:

| Consulta | `endTime` | Última vela devuelta |
|---|---|---|
| Q1 | alineado a una vela (`T`) | `T - 1 paso` (la vela `T` queda AFUERA) |
| Q2 | `T + 30s` (a mitad de la vela `T`) | `T` |
| Q3 | `T + 59,999s` (1 ms antes de que `T` cierre) | `T` |
| Q4 | `T + 1 paso` (alineado a la vela siguiente) | `T` |

Los cuatro resultados son consistentes solo con una regla: **`endTime` es
estrictamente EXCLUSIVO sobre `open_time`** (`open_time < endTime`). No es ni
inclusivo sobre `open_time`, ni depende del cierre de la vela (`open_time +
duración <= endTime` queda descartado por Q3: la vela `T`, que a esa hora todavía
no "cerró" en ese sentido, sí se devuelve).

**Bajo esta semántica confirmada, `cursor = oldest_time - 1` en `_download_range`
(app/market/ohlcv_history.py) ya era matemáticamente correcto** -- excluye
exactamente la vela ya guardada e incluye la anterior. No se cambió esa fórmula.

## 2. El hallazgo real: Bitunix omite una vela real en algunas respuestas anchas

Con la fórmula de paginación descartada como causa, se reprodujo la descarga real
(`download_missing`) contra la API en vivo, con una base temporal vacía (no se tocó
ninguna base del proyecto para esto). El patrón de huecos de 1 minuto, aislados,
cada ~200 minutos, se reprodujo de inmediato.

Rastreando página por página: **las costuras entre páginas sucesivas nunca tienen
hueco** (`seam_gap=False` en las 5 páginas trazadas). El hueco aparece DENTRO de una
sola respuesta de `limit=200` sin `startTime` -- exactamente el patrón de
`_download_range`. Verificado en vivo, tres veces:

- La misma vela, pedida con una ventana chica (`limit` bajo, con o sin
  `startTime`), **SÍ está presente**.
- La misma vela, pedida con `limit=200` y sin `startTime` (el patrón real de
  `_download_range`), **puede faltar**.
- Agregar `startTime` a la llamada ancha (`limit=200`) **no lo arregla** -- el
  mismo rango, exactamente la misma vela ausente.

No se encontró una regla determinista para predecir cuándo pasa, ni documentación
oficial de Bitunix disponible sobre esto (no se pudo verificar más allá de la propia
observación empírica, repetida). Conclusión: **no es un bug de nuestra fórmula de
paginación -- es un comportamiento de Bitunix al servir respuestas grandes**, que no
se puede corregir ajustando los parámetros de la llamada.

## 3. Corrección: verificar y reparar, no confiar solo en la paginación

En vez de perseguir una fórmula de paginación "perfecta" (no existe una que evite el
comportamiento de Bitunix), `app/market/ohlcv_history.py` agrega:

- **`find_gaps(open_times, step_ms, start=None, end=None)`**: huecos en una serie
  cacheada, puro, sin red.
- **`fill_gaps(client, db, symbol, interval, price_type, gap_times, step_ms)`**:
  repara huecos puntuales con pedidos ESTRECHOS (agrupando huecos contiguos en una
  sola llamada). Devuelve lo que de verdad se recuperó -- si ni con ventana chica
  aparece, no lo inventa, lo deja y lo reporta.
- **`download_missing`** llama a `_verify_and_repair` al final (haya tocado la red o
  no): verifica densidad y repara lo que falte. Una cache vieja con huecos de antes
  se autorepara en la siguiente llamada normal, no solo con una reparación manual.

Tests en `tests/unit/test_ohlcv_history.py`: `find_gaps`/`fill_gaps` puros, y un
cliente falso (`FlakyRestClient`) que simula el hallazgo real (respuesta ancha omite
una vela, estrecha no) para 1m, 1h, 4h y 1d -- confirmando que `download_missing`
termina sin huecos en los cuatro casos, y que un hueco genuinamente irreparable (la
vela nunca existe, ni estrecho) no hace que `download_missing` lance.

## 4. Reparación de los 3 símbolos del incidente (sobre una copia)

`scripts/repair_ohlcv_gaps.py` corrido sobre una COPIA de
`data/minerva_estabilidad.db` (nunca la base de trabajo, ruta verificada con
assert):

| Símbolo | `price_type` | Huecos | Reparados |
|---|---|---|---|
| DOGEUSDT | LAST_PRICE / MARK_PRICE | 40 / 40 | 40 / 40 |
| TRXUSDT | LAST_PRICE / MARK_PRICE | 41 / 41 | 41 / 41 |
| XRPUSDT | LAST_PRICE / MARK_PRICE | 41 / 41 | 41 / 41 |

**Total: 244 huecos, 244 reparados, 0 sin reparar.** Verificado de forma
independiente (consulta SQL directa, sin reusar `find_gaps`): 0 huecos en las 6
series tras la reparación.

**Confirmación de punta a punta:** se reconstruyó el escenario exacto del incidente
(`PositionMonitor._reconcile_trade` para la sombra TRXUSDT todavía abierta, con la
ventana completa de 5,75 días) sobre la copia ya reparada. Resultado: `"ABIERTA"` --
reconcilió con velas (`CANDLE_RECON`) sin lanzar, en vez del `CRITICAL`/`"ERROR"`
original. La reparación resuelve el incidente en el caso real.

## 5. Huecos en las series 1h/4h/1d de la Fase 2, y su efecto

Cuantificado sobre `data/backups/minerva_fase2_v2.db` (solo lectura, `mode=ro`): 60
combinaciones símbolo/intervalo/`price_type`, **236 huecos en total**, concentrados
en 2 de los 10 símbolos:

| Símbolo | Intervalo | Huecos (LAST+MARK) |
|---|---|---|
| ZECUSDT | 1h | 142 |
| ZECUSDT | 4h | 34 |
| ZECUSDT | 1d | 4 |
| LINKUSDT | 4h | 40 |
| DOGEUSDT | 1h | 8 |
| BNBUSDT | 4h | 6 |
| TRXUSDT | 4h | 2 |

**Son de una naturaleza distinta a los del incidente de estabilidad.** No son
aislados de 1 vela: son bloques contiguos (el de ZECUSDT 1h empieza con al menos 5
horas seguidas desde 2023-09-12 17:00; el de LINKUSDT 4h son 20 velas seguidas, ~3,3
días, desde 2023-10-01). Se verificó en vivo contra la API real: pedido directamente
ese rango de ZECUSDT, **Bitunix hoy tampoco devuelve esas velas** -- es un hueco real
y permanente del historial del exchange para ese período (probablemente una
simbología/par más chica, de 2023), no el hallazgo de la sección 2.

**`get_cached_or_raise` (el que usa el motor de backtest) no los detecta**: su
chequeo de integridad solo verifica que la CABEZA y la COLA del rango pedido estén
cubiertas, no que no haya huecos INTERNOS -- por diseño no revisa densidad. Por eso
estos 236 huecos nunca hicieron fallar la Fase 2 con `MissingHistoricalDataError`:
el motor corrió con esas velas simplemente ausentes de la serie, sin aviso.

**Efecto estimado (subconjunto representativo, sin relanzar el backtest completo):**
se corrió `run_backtest` + `compute_metrics` para ZECUSDT en sus 7 combinaciones de
estrategia/timeframe afectadas (las que ya reporta `docs/FASE2_REEJECUCION.md`),
sobre una copia, antes y después de intentar `fill_gaps`. `fill_gaps` repara 0 de los
77 huecos de ZECUSDT (son el hueco real de la sección 5, no el de la sección 2), así
que el resultado "antes" y "después" es **idéntico** -- no hay nada que recalcular
porque no hay nada reparable. Los resultados de ZECUSDT en los 7 casos (PF entre 0,70
y 1,13, con solo 1 a 474 operaciones según la celda) ya estaban fuera de los
criterios de aprobación por otras razones documentadas en
`docs/FASE2_REEJECUCION.md`; estos huecos de 2023 no cambian ninguna conclusión de
la Fase 2 (ninguna estrategia pasa los criterios, con o sin este dato).

**En conclusión: los resultados de la Fase 2 (`docs/FASE2_REEJECUCION.md` y
anteriores) se calcularon con estas 236 velas ausentes de la serie, sin que el
motor avisara nada en su momento.** Esto no cambia ninguna conclusión de la
Fase 2 -- el subconjunto representativo de ZECUSDT verificado arriba ya estaba
fuera de los criterios de aprobación por otras razones documentadas, con o sin
este dato -- pero es la razón por la que los resultados de la grilla deben
leerse sabiendo que un puñado de celdas corrió con menos velas de las
esperadas, sin aviso.

**Etapa 2e del incidente de estabilidad (2026-10-10), solo reporte, sin
relanzar nada:** se decidió explícitamente NO cambiar el comportamiento ni
volver a correr la grilla completa, solo avisar. `check_series_availability`
(`app/market/ohlcv_history.py`) ahora registra INFO ("sin huecos internos") o
WARNING (con la cuenta de huecos) para cada serie cuyos bordes ya daba por
completos, sin tocar su contrato de retorno (`None` o el mensaje de siempre) --
`scripts/run_backtest.py` ya la llama para validar de antemano todas las series
necesarias, así que una corrida futura queda avisada por este mismo canal sin
cambiar su código.

## 6. Etapa 2: tolerancia de reconciliación, marcado y reintentos

Aprobada tras la Etapa 1 (ver arriba), con dos añadidos: evitar que
`_verify_and_repair` reintentara huecos permanentes (como los de la sección 5)
contra la API en cada ciclo del generador de señales (resuelto arriba, sección
3: ventana acotada a lo pedido + memoria persistente de huecos irreparables
con `GAP_RETRY_COOLDOWN_HOURS`), y lo siguiente.

**a. Tolerancia recortada tras `fill_gaps`** (`app/trading/position_monitor.py`,
`_residual_gaps_are_tolerable`). Reemplaza la tolerancia anterior (hasta 2
velas faltantes, silenciosa, sin verificar nada) por una regla explícita: un
hueco residual de LAST_PRICE se tolera SOLO si es AISLADO (ningún minuto
vecino falta también), su minuto existe en MARK_PRICE, y el total no supera
`max(2, 0,1 % de la ventana)`. Si se tolera, esa vela se SUSTITUYE por la de
MARK_PRICE para evaluar SL/TP/liquidación, con `fill_source =
CANDLE_RECON_MARK_SUBSTITUTE` (distinguible de `CANDLE_RECON`). Cualquier otro
caso (hueco contiguo, no confirmado por MARK_PRICE, o por encima del tope)
sigue marcando la reconciliación como fallida.

**b. Marcar y excluir en vez de confiar en el precio en vivo**
(`app/persistence/models.py`: columnas `reconciliation_failed`,
`reconciliation_attempts` en `trades` y `shadow_trades`). Una reconciliación
fallida ya NO deja la posición "a su suerte" con el próximo tick: queda
MARCADA. `shadow_repo.get_unreliable_signal_group_keys` devuelve los
`signal_group_key` con `reconciliation_failed = 1` o `close_reason =
'RECONCILE_FAILED'`, y `ai_value_verdict`/`shadow_report.build_report` los
excluyen vía el parámetro `unreliable_keys` (`app/trading/ai_value.py`),
SEPARADO de `piloto_keys` -- son razones de exclusión distintas (fase del
experimento vs. confiabilidad del dato).

**c. Comportamiento mientras está marcada, y reintentos**
(`app/trading/position_monitor.py`):
- **Sombra**: se CONGELA (`_evaluate` no la evalúa por tick mientras
  `reconciliation_failed`), no se cierra con un precio en vivo que no es
  fiable. Se reintenta la reconciliación (al arrancar, y después cada
  `RECONCILE_RETRY_SECONDS` = 300 s vía `_retry_failed_reconciliations`,
  llamado en cada `tick_periodic`) desde la ventana de la falla ORIGINAL, no
  desde la apertura (ver correcciones de la revisión, abajo). Si tras
  `MAX_SHADOW_RECONCILE_ATTEMPTS` = 3 intentos Y `MIN_SHADOW_RECONCILE_
  GIVE_UP_HOURS` = 6h de racha fallida sigue fallando, se cierra
  administrativamente SIN PnL (`ShadowBook.close_unreliable`:
  `close_reason='RECONCILE_FAILED'`, `pnl_gross_usdt=pnl_net_usdt=0.0`,
  `exit_price=entry_price`) y queda excluida (ver b).
- **Real (futuro)**: sigue vigilada en vivo indefinidamente -- nunca se
  congela ni se cierra administrativamente, porque ocultar una exposición
  real sería peor que un precio de cierre impreciso. Queda MARCADA
  (`reconciliation_failed`) y un cierre por tick mientras lo está usa
  `fill_source = TICK_UNRECONCILED` en vez de `TICK` (mismo precio, misma
  regla de peor caso, pero auditable por separado). Sigue contando en
  `compute_open_real_positions` (features del prompt): el LLM debe ver la
  exposición real aunque esté marcada, no una posición oculta.
- Un reintento exitoso (en cualquier desenlace: sigue abierta, se cierra por
  SL/TP/liquidación con vela real, o no había ventana) limpia la marca
  (`reconciliation_failed=False`, `reconciliation_attempts=0`,
  `reconciliation_window_start_ms=None`, `reconciliation_first_failed_at_ms=
  None`) tanto en la fila persistida como en el objeto en memoria.

**d. Pruebas**: unitarias para `_residual_gaps_are_tolerable` (aislado vs.
contiguo, confirmado por MARK_PRICE vs. no, dentro/fuera del tope), la
sustitución por MARK_PRICE con `fill_source` auditable, el congelamiento de
sombras marcadas, la renombrada de `fill_source` a `TICK_UNRECONCILED` en
reales, los reintentos (éxito limpia la marca, fallo incrementa intentos) y el
cierre sin PnL al agotarlos; más un caso de punta a punta: reconciliación
falla -> sombra marcada y sin cierre por tick -> excluida del veredicto de
`ai_value_verdict`. Ver `tests/unit/test_position_monitor.py` y
`tests/unit/test_ai_value.py`.

### Revisión de la Etapa 2 (misma fecha): tres correcciones antes de reiniciar el bot

Aprobada con tres correcciones pedidas sobre la implementación de arriba,
todas en `app/trading/position_monitor.py` salvo donde se indique:

**Corrección 1 -- reintentar desde la ventana de la falla, no desde
`opened_at`.** El `_attempt_reconcile_retry` original llamaba
`_reconcile_trade(trade, None, now_ms)`, que resuelve a `start_ms = opened_ms`:
cada reintento rejugaba TODA la vida de la posición con el `effective_stop`/
`best_price` ACTUALES (ya avanzados por trailing desde que se abrió) contra
velas viejas, de ANTES de que el stop avanzara -- una mecha vieja que nunca
cruzó el stop que tenía en ese momento podía cruzar el stop de HOY y cerrar la
posición por error. Corregido con dos columnas nuevas en `trades` y
`shadow_trades`, fijadas SOLO en la primera falla de la racha actual (no se
mueven en fallas posteriores, ni entre reinicios del bot, hasta que la
reconciliación se reintente con éxito):
- `reconciliation_window_start_ms`: desde dónde debe seguir reconciliando
  cada reintento (el `start_ms` que se intentó la primera vez que falló).
- `reconciliation_first_failed_at_ms`: cuándo empezó la racha (para la
  Corrección 2).

`reconcile_on_startup` también se corrigió: si una posición YA está marcada
(p.ej. el bot se cayó de nuevo antes de resolverla), usa
`reconciliation_window_start_ms` en vez del `monitor_last_seen_ms` global
(que para esa posición ya avanzó de más). Test:
`test_retry_resumes_from_the_original_failure_window_not_from_opened_at` --
trailing ya avanzado a un stop más ajustado, una mecha vieja que cruzaría ese
stop si se rejugara desde la apertura, el reintento no la cierra porque la
ventana persistida nunca incluye esa mecha.

**Corrección 2 -- rendirse por tiempo, no solo por intentos.** 3 intentos
cada 300 s son ~10 minutos: un corte de internet o de la exchange de esa
duración descartaría sombras sanas. Nueva constante
`MIN_SHADOW_RECONCILE_GIVE_UP_HOURS = 6.0`: una sombra solo se cierra como
`RECONCILE_FAILED` cuando se cumplen LAS DOS condiciones a la vez --
`reconciliation_attempts >= MAX_SHADOW_RECONCILE_ATTEMPTS` (3) Y
`(now_ms - reconciliation_first_failed_at_ms) >= 6h`. El compromiso: una
sombra con reconciliación rota puede quedar congelada (sin aportar a la
medición, pero tampoco cerrada con PnL inventado) hasta 6 horas antes de
descartarse definitivamente -- se prefiere una demora larga a perder una
sombra sana por un corte corto. Tests con reloj controlado (`now_ms` pasado
explícitamente a cada llamada, no tiempo real): `test_shadow_does_not_give_
up_after_max_attempts_if_little_time_elapsed` (3 intentos en menos de una
hora, sigue abierta) y
`test_shadow_closes_as_reconcile_failed_after_max_attempts_and_enough_elapsed_time`
(3er intento 7h después del primero, se cierra).

**Corrección 3 -- separar RECONCILE_FAILED de los agregados del reporte**
(`app/trading/shadow_report.py`). Una sombra `RECONCILE_FAILED` tiene PnL 0
FORZADO (cierre administrativo, no un resultado de mercado real); contarla en
"cerradas"/winrate/profit factor/esperanza por estrategia diluye esas tasas
sin que sea una pérdida de verdad. `build_report` ahora separa
`trades_closed` en confiables y `RECONCILE_FAILED` ANTES de calcular
`summarize_by_strategy`/`summarize_totals` (que solo ven las confiables), y
reporta las excluidas aparte, como conteo simple ("Excluidas por
reconciliación fallida"). El veredicto de `ai_value_verdict` no cambia (ya las
excluía desde la Etapa 2b, vía `unreliable_keys`). Test:
`test_report_excludes_reconcile_failed_shadows_from_the_aggregates`.
