# Subfase 3.9: brazo ejecutado de la cuenta paper

Conecta lo que faltaba para que el reporte de paso a dinero real (3.8) tenga datos: la cuenta
**paper** abre solo las señales que el LLM etiquetó `APROBADA` cuando su decisión llegó a tiempo
(`docs/FASE3_6_LLM.md`, sección g). **100 % paper**: solo conoce a `PaperBackend`; no existe ninguna
ruta a `BitunixBackend` (hay pruebas por AST). Plan aprobado el 2026-10-17.

Código: `app/trading/paper_execution.py` (`PaperExecutionArm`), tabla `paper_execution_log`
(`app/persistence/repositories/paper_execution_repo.py`), gancho `on_decision` en
`app/trading/shadow_live_labeler.py`, cableado en `app/main.py`.

## Interruptor y activación

- `PAPER_EXECUTION_ENABLED` (default **false**). `scripts/start_bot.ps1` lo fija en `true` **solo con
  el puerto 8000** (queda en el diario `logs/medicion.log`); `scripts/run_dev_server.py` lo fuerza a
  `false`; el 8001 de prueba lo deja apagado (lo verifica la prueba de integración).
- Es **distinto** de `AUTO_OPEN_WITHOUT_LLM`, que sigue en `false` para siempre (su nombre es "sin
  LLM"). Divergencia con la sección (g), cuya condición 1 era `AUTO_OPEN_WITHOUT_LLM=true`: esa
  condición se reemplaza por este interruptor propio. Si alguien pone ambos en `true`, el brazo **se
  niega a activarse** (CRITICAL): mezclaría operaciones SIN_LLM en la misma cuenta.
- Requiere `LLM_LIVE_LABELING_ENABLED=true` (sin etiquetado no llega ninguna decisión: WARNING).
- Al activarse fija **una sola vez** `system_state.real_arm_started_at` y
  `real_arm_drawdown_baseline` (el contador de drawdown de ese momento): las claves que lee
  `scripts/real_money_report.py`. Reiniciar ese criterio es una acción manual deliberada.

## Reglas, en orden

Cada decisión `APROBADA` que llega deja **una fila** en `paper_execution_log` (`signal_group_key`
única: un grupo nunca abre dos veces). `RECHAZADA` y `SIN_LLM` no son del brazo y no dejan fila.

| # | Regla | Si no se cumple (`reason`) |
|---|---|---|
| 1 | Reclamo idempotente del grupo | (ya reclamado: no hace nada) |
| 2 | `decision_delay_s <= LLM_REAL_MAX_DELAY_SECONDS` (60 s, `real_open_allowed`) | `LATE_DECISION` |
| 3 | Scheduler no pausado | `SCHEDULER_PAUSED` |
| 4 | El símbolo no tiene una posición abierta | `POSITION_ALREADY_OPEN` |
| 5 | Spec del contrato en caché | `NO_CONTRACT_SPEC` |
| 6 | Tamaño mínimo (ver abajo) | `BELOW_MIN_ORDER` |
| 7 | Motor de riesgo (`PaperBackend.open_position`) | `RISK_<código>`: `KILL_SWITCH_ACTIVE`, `DRAWDOWN_STOP_ACTIVE`, `CIRCUIT_BREAKER_ACTIVE`, `DAILY_LOSS_LIMIT`, `DATA_STALE`, `STRATEGY_NOT_ELIGIBLE`, `MAX_SIMULTANEOUS_POSITIONS`, `MAX_SAME_DIRECTION_POSITIONS`, `SL_CAP_EXCEEDED`, `MARGIN_UNAVAILABLE` |
| 8 | Niveles todavía válidos al precio de llenado | `LEVELS_INVALID` |
| 9 | Cualquier otro fallo al abrir | `OPEN_ERROR` (solo el tipo de excepción) |
| – | Fila `PENDING` que sobrevive a un reinicio | `ABORTED_RESTART` (o `OPENED` si el trade sí se creó) |

Una apertura exitosa queda `OPENED` con `trade_id` y, en `details_json`, `entry_delay_s` (cierre de
vela → apertura), `shadow_entry_price`, `executed_entry_price` y `adverse_gap_bps` (positivo = peor que
la sombra). La operación se marca `decision_source='LLM_APROBADA'`, con margen `DEFAULT_MARGIN_USDT` y la
estrategia representante (la primera elegible del grupo). Los rechazos del motor de riesgo también
quedan en `risk_rejections`, como siempre.

**Esperable:** ~3 de cada 4 aprobadas saldrán `RISK_STRATEGY_NOT_ELIGIBLE` (la cuenta solo opera
`ema_cross_9_21` y las dos de funding); con la copia del 2026-10-17 (muestra chica) eso deja unas **2-3
aperturas por día en marcha**.

### Regla de tamaño mínimo

**Omitir y registrar, sin tocar el margen.** Antes de abrir se calcula la qty (`margen × apalancamiento /
precio de la sombra`), se redondea **hacia abajo a `basePrecision`** (como lo haría el exchange) y se
compara con `minTradeVolume`; si queda por debajo, `BELOW_MIN_ORDER` con `{symbol, price, qty,
qty_floored, min_trade_volume, base_precision}`. Esto es **más estricto** que el chequeo propio de
`PaperBackend` (que no redondea). Ejemplo real: `TRXUSDT` a 0,336 → 148,8 TRX → 148 < 150: no se abre.
`DEFAULT_MARGIN_USDT` no cambia (5). Si el motor de riesgo recorta el margen y eso rompe el mínimo,
también queda `BELOW_MIN_ORDER`.

*Alternativa para decidir con datos más adelante (no implementada):* subir el margen solo lo necesario
para llegar al mínimo con un tope (TRX pediría ~5,03 USDT). *No* se redondea la qty del trade a
`basePrecision`: cambiaría `compute_open_fill`, compartido con la sombra, y rompería "mismo cálculo en
ambas cuentas". El desfase de nocional por redondeo (BTC/ETH hasta ~6 %) queda como riesgo conocido.

## Aislamiento de la medición

- La etiqueta ya está persistida cuando el brazo se entera: `ShadowLiveLabeler._label_one` llama al
  gancho **después** de `decide_group`. El brazo corre en una **tarea propia** (`submit` no lanza nunca);
  un fallo o un cuelgue suyo no afecta al etiquetado, al scheduler ni al monitor.
- **No toca**: `app/llm/prompts.py` (`SYSTEM_PROMPT`, `FEATURES_VERSION`), `FROZEN_PROMPT_SHA256`,
  `LlmDecisionService`, `ShadowBook`, `PaperBackend`, `risk_engine`. **No escribe** en `shadow_trades`
  ni `llm_logs` (por eso `executed_in_real_account` queda en 0: el vínculo vive en
  `paper_execution_log`).
- Pruebas: etiqueta persistida antes de avisar; gancho que lanza, brazo que lanza o que cuelga;
  `shutdown` cancela; con el interruptor apagado no se crea nada; **la medición es idéntica con el brazo
  encendido y apagado** (toda la fila de cada sombra, `llm_logs` y el avance); el brazo solo escribe en
  `paper_execution_log`, `trades`, `system_state` y `risk_rejections`.

## Resultados ocultos hasta el registro oficial

Con el brazo activo, `trades` contiene operaciones de señales `APROBADA`: su PnL es la mitad `APROBADA`
del veredicto. Decisión del 2026-10-17: siguen la **misma regla** que las sombras
(`is_outcome_reveal_allowed`: gate abierto + registro oficial).

- `GET /trades` y `GET /positions` (sin login) dejan `response_model=list[Trade]` y devuelven una lista
  blanca: siempre estructura (símbolo, lado, estrategia, estado, margen, qty, entrada, SL/TP/liq,
  `decision_source`); solo con el reveal: `pnl_*`, `exit_price`, `close_reason`, `closed_at`, comisiones,
  funding, slippage, `best_price`, `effective_stop`, `fill_source`.
- Telegram: `/status` sin balance ni equity, `/balance` solo capital inicial y posiciones,
  `/positions` sin PnL flotante, `/metrics` solo el conteo de cerradas. Siguen visibles el estado de
  riesgo, los conteos y los motivos de omisión.
- **Residual declarado:** los logs INFO (`Monitor cerro ... motivo=`, `Posicion cerrada ... pnl_neto=`)
  siguen mostrando motivo y PnL; ya pasaba con las sombras y no se tocan.

## Cómo verlo (sin PnL)

- `GET /health` → bloque `paper_execution: {enabled, active, armed_since, opened_today, skipped_today}`.
- Telegram `/status` → una línea "Brazo ejecutado (paper): activo | abiertas hoy N | omitidas hoy M".
- `python scripts/paper_execution_status.py --db <copia>`: conteo por resultado y motivo, calidad de la
  entrada (retraso y diferencia de precio frente a la sombra), símbolos no ejecutables y últimas
  decisiones. Solo lectura, ruta distinta de la base de trabajo.
- `scripts/real_money_report.py` (con el reveal permitido) añade las omitidas por motivo y la entrada
  ejecutada vs sombra.

## Plan de despliegue (lo ejecutas tú; no se reinicia el bot del 8000 desde el asistente)

**Un solo reinicio** (`stop_bot.ps1` + `start_bot.ps1`) en una hora **que no sea de cierre de 4 h** y
entre los minutos **:10 y :40**. Todas las filas de medición caen en :00/:01, así que desde :10 ya no
queda etiquetado en vuelo que el cierre cancele y la siguiente tanda es ≥ 20 min después; la vela
evaluada al arrancar ya existe y no genera grupos nuevos. Las velas de 4 h cierran a las 00/04/08/12/16/20
UTC (la de 1 d a las 00). Horas UTC válidas: 01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19,
21, 22, 23. En hora de Chile (UTC-3 hasta abril): **10, 11, 12, 14, 15, 16, 18, 19, 20 h** (evitar 21,
01, 05, 09, 13, 17). Ejemplo: 11:10-11:40.

Verificación posterior (solo lectura):

1. `GET http://127.0.0.1:8000/health` en ≤ 2 min: `status: ok`, `llm_labeling.frozen_hash_ok: true`,
   `llm_labeling.enabled: true`, `paper_execution.enabled: true`, `active: true`, `armed_since` con la
   hora del reinicio.
2. `logs/minerva.log`: línea "Brazo ejecutado (paper) ACTIVO" y **ningún** WARNING de hash distinto del
   congelado ni "Tarea de etiquetado en vivo fallo".
3. Tras el siguiente :00 (≤ 1 h), con una copia fresca: `python scripts/measurement_status.py --db
   <copia>` → suben las filas de MEDICION, "Grupos omitidos" sigue en 0 (el etiquetado sigue intacto).
4. Primera apertura: `python scripts/paper_execution_status.py --db <copia>` → filas
   `SKIPPED/RISK_STRATEGY_NOT_ELIGIBLE` (la mayoría) y, con la primera aprobada elegible a tiempo, una
   `OPENED` con `trade_id`; Telegram `/positions` la lista sin PnL. Puede tardar horas.
5. **Reversa** (flag en `false` en `start_bot.ps1` y reiniciar, otra vez en :10-:40 de una hora sin cierre
   de 4 h): `frozen_hash_ok=false`, último ciclo > 90 min sin pausa a propósito, una posición que no
   debería haberse abierto o cualquier aviso de fuga de resultados. `/kill` frena las entradas pero
   también **detiene la medición**, así que no es el freno.

## Límites y riesgos que quedan

- El retraso real de apertura es `decision_delay_s` + 1-9 s de LLM + la llamada de precio; se mide en
  `entry_delay_s` y no cambia la regla de los 60 s.
- Una posición recién abierta entra al monitor en ≤ `MONITOR_TICK_SECONDS` (15 s).
- Las llamadas REST del brazo comparten el limitador de Bitunix con el generador de señales (impacto
  menor: 2-3 aperturas al día).
- Si el bot se apaga, el brazo pierde el tiempo igual que la medición; `real_arm_started_at` no se
  reinicia solo y los 30 días del criterio de la 3.8 cuentan calendario desde esa marca.
- 100 operaciones cerradas del brazo ≈ 35-50 días *en marcha* (orden de magnitud, muestra chica).
