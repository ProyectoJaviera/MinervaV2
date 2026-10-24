# Guía del paquete `trading`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/trading.html`](../api/app/trading.html)

## Qué hace el paquete

Es el núcleo operativo y el paquete más grande: genera las señales, abre las **sombras**, pide al LLM la etiqueta, abre en
la cuenta paper las aprobadas a tiempo, controla el riesgo, cierra todo por SL/TP/trailing/liquidación y **mide** el valor
de la IA sin mirar resultados antes de tiempo. Casi todas las zonas protegidas de la medición están aquí.

Flujo de una señal (detalle en [FLUJO_EJECUCION](../FLUJO_EJECUCION.md)):

1. `signal_generator` evalúa las estrategias y agrupa las señales accionables en `SignalCandidate`.
2. `shadow_book` abre **una sombra** por candidato (entra al precio de cierre de la vela, sin cupos).
3. `shadow_live_labeler` pide al LLM su etiqueta (`APROBADA` / `RECHAZADA` / `SIN_LLM`) y la guarda una sola vez.
4. `paper_execution` abre en la cuenta paper las `APROBADA` que llegaron a tiempo, por `risk_engine` y `PaperBackend`.
5. `position_monitor` cierra sombras y posiciones por SL, TP, trailing o liquidación.
6. `measurement_status`, `ai_value*` y `verdict_record` miden y deciden el veredicto; `reveal` oculta los resultados hasta
   entonces.

## Zona protegida

**La mayor parte.** Lo que decide qué se mide o cómo se mide no se puede cambiar mientras la medición corre. Cada archivo
indica su caso; el detalle y el porqué están en [ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md). Resumen: **protegidos**
`signal_generator`, `levels`, `sl_calc`, `slippage`, `fills`, `funding_accrual`, `stop_engine`, `shadow_book`,
`shadow_live_labeler`, `measurement_status`, `ai_value`, `ai_value_placebo`, `ai_value_report`, `verdict_record` y
`reveal`; **se pueden tocar con cuidado** (afectan a la cuenta paper, no a la medición) `risk_engine`, `paper_execution`
y `control_*`; `shadow_report` y `real_money_report` son informes que se niegan a mostrar resultados.

## Archivos

### `app/trading/__init__.py`

**Responsabilidad.** Docstring del paquete: el flujo de arriba y el aviso de que concentra las zonas protegidas.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/trading/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading.html`](../api/app/trading.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_build_dev_docs.py`, `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:app/trading/__init__.py -->

### `app/trading/ai_value.py`

**Responsabilidad.** La **regla de decisión** del valor de la IA: agrupa las sombras cerradas en conglomerados, calcula la
diferencia de esperanza APROBADA − RECHAZADA con un intervalo de confianza del 95 % por bootstrap de conglomerados y
aplica la regla de tres veredictos. También la calibración por permutación (placebo).

**Funciones y clases clave.**
- `assign_clusters()` — dos operaciones del mismo símbolo y dirección cuyos intervalos de vida [apertura, cierre] se
  solapan son **un** conglomerado (solo operaciones cerradas).
- `build_units()` y `cluster_bootstrap_difference()` — una unidad por conglomerado con los retornos
  (`pnl_net / margen`) de sus aprobadas y rechazadas; bootstrap de `BOOTSTRAP_RESAMPLES` (2.000) con semilla fija.
- `ai_value_verdict()` — la regla: `APORTA_VALOR` si el límite inferior del IC es > 0 (con `magnitud_baja` si además el
  superior es < δ); `NO_APORTA_VALOR` si el superior es < δ; `INCONCLUSO` en el resto, con N efectivo insuficiente, sin
  remuestreos válidos o con más de `MAX_SIN_LLM_SHARE` (10 %) de `SIN_LLM`, condición que se mira **antes** que el IC.
  Recibe `measurement_keys`, `piloto_keys` y `unreliable_keys` para filtrar.
- `placebo_calibration()` — relabela al azar cada conglomerado con la tasa de aprobación observada y repite la regla
  `PLACEBO_REPEATS` (1.000) veces.

**Invariantes — no romper.**
- Constantes **fijadas antes de ver datos**: `MIN_EFFECTIVE_N = 300`, `DELTA_PCT = 0.03`, `MAX_SIN_LLM_SHARE = 0.1`,
  `BOOTSTRAP_RESAMPLES = 2000`, `BOOTSTRAP_SEED = 20261004`, `CI_LEVEL = 0.95`, `MIN_VALID_RESAMPLES = 50`,
  `PLACEBO_REPEATS = 1000`, `PLACEBO_SEED = 20261009`, `PLACEBO_CALIBRATION_THRESHOLD = 0.05`.
- Δ se mide sobre `pnl_net_usdt / margin_usdt` (fracción del margen), no sobre USDT: así vale con cualquier margen.
- Solo operaciones **cerradas** entran en Δ; el `SIN_LLM` se cuenta sobre **todos** los grupos.
- `SIN_LLM` nunca es `RECHAZADA`.
- La función **no toca la base**: los filtros de claves son del llamador.

**Trampas conocidas.**
- La sombra permite solapes (el backtest no): sin bloqueo las operaciones no son independientes, y los conglomerados son
  la corrección. Cambiar la definición de conglomerado cambia el N efectivo y, con él, cuándo se abre el gate.
- `measurement_keys` descarta las sombras que nunca pasaron por el LLM de esta medición (anteriores a la 3.6 o de los
  pilotos); sin ese filtro el `SIN_LLM` parecería 100 % para siempre.

**Protección.** Sí: [Gate, placebo y veredicto](../ZONAS_PROTEGIDAS.md#gate-placebo-y-veredicto).

<!-- hechos:app/trading/ai_value.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/ai_value.html`](../api/app/trading/ai_value.html)
- **Importa (internos):** `app.persistence.models`
- **Lo importan:** `app.trading.ai_value_placebo`, `app.trading.ai_value_report`, `app.trading.measurement_status`, `app.trading.real_money_report`, `app.trading.shadow_report`, `scripts.measurement_status`, `scripts.placebo_check`, `scripts.shadow_report`, `scripts.telegram_daily`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_ai_value.py`, `tests/unit/test_ai_value_placebo.py`, `tests/unit/test_placebo_check_script.py`, `tests/unit/test_position_monitor.py`
<!-- /hechos:app/trading/ai_value.py -->

### `app/trading/ai_value_placebo.py`

**Responsabilidad.** Corre el placebo sobre una **copia** de la base (solo lectura) con **exactamente los mismos filtros**
que el veredicto, y decide si el procedimiento está calibrado.

**Funciones y clases clave.**
- `build_placebo()` — carga los insumos con `ai_value_report.load_verdict_inputs` y llama a `ai_value.placebo_calibration`.
- `prob_both_sides_reach()` — probabilidad **exacta** (binomial) de que una relabelación al azar deje al menos
  `min_n` conglomerados en cada lado.
- `classify_calibration()` — `CALIBRADO`, `NO_CALIBRADO` o `NO_INFORMATIVO` con la tasa **condicional** (frecuencia de
  `APORTA_VALOR` por azar dividida por la probabilidad de llegar al análisis).
- `render_placebo()` — el informe de texto, con el bloque para copiar al registro.

**Invariantes — no romper.**
- Criterio decidido el 2026-10-17, antes de ver datos: calibrado si la tasa condicional es < 5 % **y** la probabilidad de
  llegar al punto de análisis es ≥ `INFORMATIVE_VALID_FRACTION` (50 %); con menos, es `NO_INFORMATIVO` (ni sí ni no) y se
  esperan 7 días más de datos.
- El placebo **no revela nada de la IA** (las etiquetas reales se sustituyen por azar): puede correr antes de que el gate
  se abra, y debe registrarse **antes** de la primera decisión real.

**Trampas conocidas.** La frecuencia bruta y su IC de Wilson se imprimen solo como información; el `calibrated` de
`ai_value.PlaceboResult` ya no decide.

**Protección.** Sí: [Gate, placebo y veredicto](../ZONAS_PROTEGIDAS.md#gate-placebo-y-veredicto).

<!-- hechos:app/trading/ai_value_placebo.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/ai_value_placebo.html`](../api/app/trading/ai_value_placebo.html)
- **Importa (internos):** `app.trading.ai_value`, `app.trading.ai_value_report`, `app.trading.shadow_live_labeler`
- **Lo importan:** `scripts.placebo_check`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_ai_value_placebo.py`, `tests/unit/test_placebo_check_script.py`
<!-- /hechos:app/trading/ai_value_placebo.py -->

### `app/trading/ai_value_report.py`

**Responsabilidad.** El informe del veredicto, en solo lectura sobre una copia. Es **el único lugar que lee resultados**
(PnL, precio de salida, motivo de cierre), y solo después de pasar la guardia del gate.

**Funciones y clases clave.**
- `build_report()` — empieza con `compute_status` (consultas sin PnL); si el gate está cerrado devuelve sin haber
  ejecutado nunca la consulta que lee resultados (`_load_outcome_trades`). Con el gate abierto pero un N efectivo
  < `MIN_EFFECTIVE_N` tras las exclusiones, declara «punto de análisis no alcanzado» y suprime Δ, IC y desglose.
- `load_verdict_inputs()` — carga y filtra las sombras (hash congelado, sin piloto, sin `RECONCILE_FAILED`): lo comparten
  el veredicto y el placebo.
- `compute_partition()`, `compute_omitted_profile()`, `compute_sin_llm_diagnostics()`, `compute_sensitivity()` y
  `compute_breakdown()` — las secciones del informe: partición de grupos, perfil de los omitidos, `SIN_LLM` por causa/franja/
  volatilidad, veredicto sin las filas con JSON reparado y desglose exploratorio.
- `assert_synthetic_database()` / `check_frozen_hash_mode()` — la bandera de pruebas solo se acepta con una base
  **positivamente sintética** (modelo `synthetic-test`, sin el hash congelado real).

**Invariantes — no romper.**
- La guardia del gate es **estructural**: la consulta que lee resultados está detrás de ella y un test comprueba que no se
  ejecuta antes.
- La bandera de pruebas nunca puede destapar resultados reales: una copia real siempre tiene el modelo y el hash reales.

**Trampas conocidas.** El gate de `measurement_status` no excluye `RECONCILE_FAILED` ni el piloto, pero el veredicto sí:
por eso hay una segunda guardia, estricta.

**Protección.** Sí: [Gate, placebo y veredicto](../ZONAS_PROTEGIDAS.md#gate-placebo-y-veredicto).

<!-- hechos:app/trading/ai_value_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/ai_value_report.html`](../api/app/trading/ai_value_report.html)
- **Importa (internos):** `app.persistence.llm_log_fields`, `app.persistence.models`, `app.persistence.repositories.shadow_repo`, `app.trading.ai_value`, `app.trading.measurement_status`, `app.trading.shadow_live_labeler`
- **Lo importan:** `app.trading.ai_value_placebo`, `app.trading.real_money_report`, `scripts.ai_value_report`, `scripts.lessons_report`, `scripts.placebo_check`, `scripts.real_money_report`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `llm_logs`, `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/synthetic_measurement.py`, `tests/unit/test_ai_value_placebo.py`, `tests/unit/test_ai_value_report.py`, `tests/unit/test_atr_from_prompt.py`
<!-- /hechos:app/trading/ai_value_report.py -->

### `app/trading/control_actions.py`

**Responsabilidad.** Las acciones de control compartidas por Telegram y el dashboard (`pause`, `resume`, `kill`) con la
**misma semántica** y un rastro auditable en `control_audit`.

**Funciones y clases clave.**
- `apply_control()` — aplica una acción y deja su rastro (quién, cuándo, por qué canal). Un candado por base evita que dos
  acciones simultáneas (doble clic, dashboard y Telegram a la vez) se pisen.
- `read_state()`, `ControlState`, `ControlResult` — estado actual (scheduler pausado, kill switch) y resultado.
- `STOPS_MEASUREMENT`, `RESUME_LIFTS`, `DASHBOARD_WARNINGS`, `CONFIRM_PHRASES` — los textos de advertencia y las palabras de
  confirmación (`PAUSAR`, `REANUDAR`, `KILL`), únicos para ambos canales.

**Invariantes — no romper.**
- **Pausar el scheduler detiene la medición** (sin señales, sin sombras, sin etiquetado). `kill` activa el kill switch **y**
  pausa el scheduler; `resume` levanta la pausa **y** el kill switch, pero **no** el stop por drawdown
  (`risk_engine.resume_drawdown_stop`).
- Solo escribe `system_state` y `control_audit`: no abre ni cierra posiciones, no toca sombras, etiquetas ni el prompt.

**Trampas conocidas.** La pausa nunca es silenciosa: `Scheduler.last_cycle_at` deja de avanzar y `/health` pasa a `stale`.

**Protección.** No (se puede tocar), pero cambiar qué detiene cada acción afecta a la medición.

<!-- hechos:app/trading/control_actions.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/control_actions.html`](../api/app/trading/control_actions.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.repositories.control_audit_repo`, `app.trading.control_state`, `app.trading.risk_engine`
- **Lo importan:** `app.api.routes.control`, `app.telegram.commands`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** `control_audit`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_base.py`, `tests/unit/test_phase5_control_api.py`, `tests/unit/test_phase5_push.py`
<!-- /hechos:app/trading/control_actions.py -->

### `app/trading/control_state.py`

**Responsabilidad.** La pausa persistente del scheduler: `system_state.scheduler_paused`.

**Funciones y clases clave.**
- `is_scheduler_paused()` y `set_scheduler_paused()`.

**Invariantes.** La pausa **sobrevive a un reinicio** a propósito (una pausa pedida por Telegram no debe «olvidarse»). No
detiene el monitor de posiciones ni los cierres, que son independientes del scheduler.

**Trampas conocidas.** El kill switch (`risk_engine`) es otra cosa: bloquea entradas nuevas de la cuenta paper, no señales ni
sombras; es `control_actions.apply_control` el que, para la acción `kill`, también pausa.

**Protección.** No.

<!-- hechos:app/trading/control_state.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/control_state.html`](../api/app/trading/control_state.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.repositories.system_state_repo`
- **Lo importan:** `app.api.routes.health`, `app.api.routes.risk`, `app.core.scheduler`, `app.telegram.commands`, `app.telegram.push`, `app.trading.control_actions`, `app.trading.paper_execution`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `system_state`
- **Tablas que escribe:** `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_controls.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_phase5_base.py`, `tests/unit/test_phase5_control_api.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_scheduler_pause.py`, `tests/unit/test_telegram_commands.py`, `tests/unit/test_telegram_control.py`, `tests/unit/test_telegram_e2e.py`
<!-- /hechos:app/trading/control_state.py -->

### `app/trading/fills.py`

**Responsabilidad.** Liquidar un cierre con **la misma fórmula** para la cuenta paper y las sombras: comisión de salida,
slippage de salida y PnL bruto/neto, sumando el funding acumulado.

**Funciones y clases clave.**
- `settle_close()` y `Settlement`.

**Invariantes — no romper.** Es compartida a propósito para que una señal tenga exactamente el mismo PnL en ambas cuentas.

**Trampas conocidas.** Ninguna conocida.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/trading/fills.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/fills.html`](../api/app/trading/fills.html)
- **Importa (internos):** `app.execution.pnl`, `app.persistence.models`, `app.trading.slippage`
- **Lo importan:** `app.execution.paper_backend`, `app.trading.shadow_book`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/trading/fills.py -->

### `app/trading/funding_accrual.py`

**Responsabilidad.** El funding acumulado de una posición (real o sombra) a partir de los eventos de `funding_cache`.

**Funciones y clases clave.**
- `compute_funding_accrual()` — el funding nuevo hasta un instante; **no escribe** nada.
- `accrue_funding()` — lo aplica y guarda en una posición de la cuenta paper (las sombras usan
  `ShadowBook.accrue_funding`).

**Invariantes — no romper.**
- Ningún evento se cuenta dos veces: `funding_last_applied_ms` guarda el último evento aplicado.
- Convención (igual que el backtest): una tasa positiva hace que LONG pague a SHORT; el costo es `nocional × tasa`, con signo
  +1 para LONG y −1 para SHORT, y `funding_paid_usdt` positivo significa que la posición paga.

**Trampas conocidas.** Lo usan el monitor en vivo, la reconciliación al reiniciar y el cierre.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/trading/funding_accrual.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/funding_accrual.html`](../api/app/trading/funding_accrual.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.funding_repo`, `app.persistence.repositories.trades_repo`
- **Lo importan:** `app.execution.paper_backend`, `app.trading.position_monitor`, `app.trading.shadow_book`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `funding_cache`
- **Tablas que escribe:** `trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_slippage_and_funding_accrual.py`
<!-- /hechos:app/trading/funding_accrual.py -->

### `app/trading/levels.py`

**Responsabilidad.** Resolver SL, TP, trailing y liquidación de una posición **al precio real de llenado** (no al de la
señal).

**Funciones y clases clave.**
- `resolve_trade_levels()` — respeta los niveles absolutos de la estrategia; completa los que faltan con el SL/TP porcentual
  de respaldo (si la estrategia define trailing no se agrega TP de respaldo); calcula la liquidación siempre
  (`compute_liquidation_price`).
- `StrategyLevels` (lo crudo de la estrategia) y `TradeLevels` (lo resuelto).

**Invariantes — no romper.**
- Lanza `ValueError` si el SL ya fue superado al llenar o el TP está del lado equivocado: la posición se cerraría en el
  primer tick sin haber existido; el llamador descarta la entrada.
- `planned=None` (entrada manual) no lleva SL ni TP.

**Trampas conocidas.** La sombra y la cuenta paper llaman a la misma función con precios distintos (cierre de la vela vs.
mercado al abrir), por eso sus niveles pueden diferir ligeramente.

**Protección.** Sí: [Niveles de SL y TP](../ZONAS_PROTEGIDAS.md#niveles-de-sl-y-tp).

<!-- hechos:app/trading/levels.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/levels.html`](../api/app/trading/levels.html)
- **Importa (internos):** `app.backtesting.liquidation`, `app.config`, `app.persistence.models`, `app.strategies.base`, `app.trading.sl_calc`
- **Lo importan:** `app.execution.paper_backend`, `app.trading.shadow_book`, `app.trading.signal_generator`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_account_results_hidden.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_shadow_book.py`, `tests/unit/test_signal_generator.py`
<!-- /hechos:app/trading/levels.py -->

### `app/trading/measurement_status.py`

**Responsabilidad.** El avance de la medición bajo el hash congelado y **la única fuente de verdad** de si el gate está
abierto y de si se pueden mostrar resultados. **Nunca mira PnL ni resultados**: solo conteos de `llm_logs` y las marcas de
tiempo y lado de `shadow_trades` necesarias para agrupar en conglomerados.

**Funciones y clases clave.**
- `compute_status()` (síncrona, sobre una conexión `sqlite3` de solo lectura: la usa `scripts/measurement_status.py`) y
  `compute_status_async()` (sobre la `Database` del bot: la usan la API y Telegram). Misma lógica, solo cambia el acceso.
- `is_measurement_gate_open()` — el **único** lugar que decide si se juntaron `MIN_EFFECTIVE_N` por lado.
- `is_outcome_reveal_allowed()` — **condición única** para mostrar resultados: gate abierto **y** registro oficial del
  veredicto (`verdict_record`). Abrir el gate no basta.
- `cluster_effective_counts()`, `running_seconds()`/`parse_eval_marks()` (ritmo sobre tiempo **en marcha**), `pace_lines()` y
  `MeasurementStatus`.

**Invariantes — no romper.**
- Nunca leer `pnl_net_usdt`, `pnl_gross_usdt`, `exit_price` ni `close_reason`.
- Banda de aprobación `APPROVAL_BAND = (0.15, 0.85)`; ritmo sobre `RATE_WINDOW_DAYS = 7.0`; un hueco de más de
  `OFFLINE_GAP_HOURS = 2.0` entre marcas de evaluación cuenta **entero** como apagado.
- Si la copia no tiene marcas de evaluación, el ritmo en marcha es `None` («sin datos»), no 0 %.

**Trampas conocidas.** El ritmo de calendario subestima lo que hace el bot cuando corre (el equipo estuvo apagado ~58 h del 15
al 17 de octubre); por eso se muestran los dos ritmos con rótulos explícitos.

**Protección.** Sí: [Gate, placebo y veredicto](../ZONAS_PROTEGIDAS.md#gate-placebo-y-veredicto) y
[Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados).

<!-- hechos:app/trading/measurement_status.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/measurement_status.html`](../api/app/trading/measurement_status.html)
- **Importa (internos):** `app.llm.schemas`, `app.persistence.database`, `app.trading.ai_value`, `app.trading.shadow_live_labeler`, `app.trading.verdict_record`
- **Lo importan:** 14 módulos (api 4, memory 1, scripts 5, telegram 1, trading 3)
- **Configuración que lee:** ninguno
- **Tablas que lee:** `llm_logs`, `shadow_trades`, `signals`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_no_leaks.py`, `tests/unit/test_ai_value_report.py`, `tests/unit/test_measurement_status_script.py`, `tests/unit/test_measurement_uptime.py`, `tests/unit/test_outcome_reveal.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_readonly_api_routes.py`, `tests/unit/test_telegram_daily_script.py`
<!-- /hechos:app/trading/measurement_status.py -->

### `app/trading/paper_execution.py`

**Responsabilidad.** El **brazo ejecutado** de la cuenta paper: abre en la cuenta paper **solo** las señales que el LLM
etiquetó `APROBADA` y cuya decisión llegó a tiempo, por el motor de riesgo y `PaperBackend`. **100 % paper.**

**Funciones y clases clave.**
- `PaperExecutionArm.activate()` — arma el brazo una vez desde el `lifespan`: se niega con `PAPER_EXECUTION_ENABLED` apagado
  o con `AUTO_OPEN_WITHOUT_LLM` encendido (mezclaría dos fuentes de apertura), resuelve las filas `PENDING` que dejó un
  reinicio y escribe **una sola vez** `real_arm_started_at` y `real_arm_drawdown_baseline`.
- `PaperExecutionArm.submit()` — lo llama el etiquetador **después** de persistir la etiqueta; crea una tarea propia y
  **no lanza jamás**.
- `PaperExecutionArm.handle()` — las reglas, en este orden, dejando el motivo en `paper_execution_log`: etiqueta ≠ APROBADA
  (nada); reclamo idempotente del grupo; `LATE_DECISION`; `SCHEDULER_PAUSED`; `POSITION_ALREADY_OPEN`; `NO_CONTRACT_SPEC`;
  `BELOW_MIN_ORDER` (con la cantidad redondeada hacia abajo a `basePrecision`, **sin cambiar el margen**); y por fin
  `open_position(decision_source="LLM_APROBADA")`, cuyo rechazo de riesgo queda como `RISK_<código>`, un nivel superado como
  `LEVELS_INVALID` y cualquier otro fallo como `OPEN_ERROR`.
- `check_order_size()`, `floor_to_precision()`, `adverse_gap_bps()` y `health_block()` (el bloque `paper_execution` de
  `GET /health`, sin PnL).

**Invariantes — no romper.**
- **Aislada de la medición:** no escribe en `shadow_trades`, `llm_logs` ni `trades` por fuera de
  `PaperBackend.open_position`; su único estado propio es `paper_execution_log`. Un fallo o un cuelgue suyo no alcanza al
  etiquetado, al scheduler ni al monitor.
- Solo conoce a `PaperBackend`: nunca importa `BitunixBackend` (prueba por AST).
- Un grupo nunca abre dos veces (reclamo idempotente por `signal_group_key`).

**Trampas conocidas.**
- `real_arm_started_at` no se reinicia solo: reiniciar el criterio de la subfase 3.8 es una acción manual y deliberada.
- Si el bot se apaga, el brazo pierde el tiempo igual que la medición.
- La mayoría de las aprobadas se omiten como `RISK_STRATEGY_NOT_ELIGIBLE` (solo tres estrategias son elegibles).

**Protección.** No para la medición; es la cuenta paper. Cambiar sus reglas reinicia de hecho el criterio de dinero real.

<!-- hechos:app/trading/paper_execution.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/paper_execution.html`](../api/app/trading/paper_execution.html)
- **Importa (internos):** 15 módulos (config 1, core 1, execution 2, llm 2, persistence 6, trading 3)
- **Lo importan:** `app.api.deps`, `app.api.routes.arm`, `app.api.routes.health`, `app.main`
- **Configuración que lee:** `AUTO_OPEN_WITHOUT_LLM`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `LLM_LIVE_LABELING_ENABLED`, `LLM_REAL_MAX_DELAY_SECONDS`, `PAPER_EXECUTION_ENABLED`, `REPORT_TIMEZONE`, `TAKER_FEE_PCT`
- **Tablas que lee:** `contract_specs_cache`, `paper_execution_log`, `system_state`, `trades`
- **Tablas que escribe:** `paper_execution_log`, `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_isolation.py`, `tests/e2e/world.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/trading/paper_execution.py -->

### `app/trading/position_monitor.py`

**Responsabilidad.** El **único** que cierra posiciones por SL, TP, trailing y liquidación (el generador solo abre).
Vigila a la vez las posiciones de la cuenta paper y las sombras.

**Funciones y clases clave.**
- `PositionMonitor.on_tick()` / `_evaluate()` — con cada tick del WebSocket evalúa los niveles en **modo tick**.
- `PositionMonitor.tick_periodic()` — cada `MONITOR_TICK_SECONDS`: guarda el trailing pendiente, recarga posiciones,
  reintenta reconciliaciones fallidas, consulta por REST los símbolos sin ticks recientes, refresca marcas y funding,
  actualiza el equity y marca `monitor_last_seen_ms`.
- `PositionMonitor.reconcile_on_startup()` — reproduce con velas de 1 minuto el periodo en que el proceso estuvo caído.
- `PositionMonitor.start_feed()` / `handle_ws_message()` — el latido del feed (`data_source_health`, `ws_feed`).

**Invariantes — no romper.**
- Reglas de relleno (decididas con el autor): SL y trailing al precio observado en el tick que cruza (peor caso); TP al
  nominal salvo hueco evidente (`tp_tick_fill_price`); liquidación solo si el último precio cruza el umbral **y** el mark por
  REST lo confirma; reconciliación con velas (`CANDLE_RECON`) al precio nominal, igual que el backtest. Todo cierre lleva su
  `fill_source` (`TICK`, `CANDLE_RECON`, `CANDLE_RECON_MARK_SUBSTITUTE`, `REST_MARK`, `TICK_UNRECONCILED`).
- **Nunca bloquea cierres.** Solo el motor de riesgo bloquea aperturas.
- Una sombra con reconciliación fallida queda **congelada** (no se cierra con un precio en vivo poco fiable) hasta que un
  reintento (cada `RECONCILE_RETRY_SECONDS`, 300 s) la reconcilie. Solo se rinde, y se cierra sin PnL como
  `RECONCILE_FAILED` (**fuera del veredicto**), cuando se cumplen **a la vez** `MAX_SHADOW_RECONCILE_ATTEMPTS` (3
  intentos) y `MIN_SHADOW_RECONCILE_GIVE_UP_HOURS` (6 h de racha fallida continua): así un corte de red de unos minutos no
  descarta sombras sanas. Una posición **real** nunca se cierra sola: sigue vigilada, solo marcada (`TICK_UNRECONCILED`).
- Los ids de posiciones reales y de sombras son independientes: la clave interna es `(es_sombra, id)`.

**Trampas conocidas.** Los huecos de LAST_PRICE tras reparar solo se sustituyen por MARK_PRICE si son pocos
(`RECONCILE_GAP_TOLERANCE_*`), aislados y existen en MARK_PRICE; si no, la reconciliación se marca como fallida en vez de
cerrar a ciegas. Nació del incidente de estabilidad del 2026-10-10.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/trading/position_monitor.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/position_monitor.html`](../api/app/trading/position_monitor.html)
- **Importa (internos):** 19 módulos (backtesting 1, config 1, core 1, execution 1, market 3, persistence 8, trading 4)
- **Lo importan:** `app.main`
- **Configuración que lee:** `FUNDING_STALE_MARGIN_HOURS`, `MONITOR_TICK_SECONDS`, `TICK_STALE_SECONDS`, `TP_GAP_TOLERANCE_PCT`, `WS_STALE_AFTER_SECONDS`
- **Tablas que lee:** `contract_specs_cache`, `ohlcv_cache`, `system_state`, `trades`
- **Tablas que escribe:** `data_source_health`, `system_state`, `trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_restart.py`, `tests/e2e/world.py`, `tests/unit/test_memory_isolation.py`, `tests/unit/test_news_runtime_isolation.py`, `tests/unit/test_phase5_push.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_scheduler_pause.py`, `tests/unit/test_shadow_book.py`, `tests/unit/test_telegram_runtime.py`
<!-- /hechos:app/trading/position_monitor.py -->

### `app/trading/real_money_report.py`

**Responsabilidad.** El reporte de paso a dinero real (subfase 3.8). **Solo recomienda**: no importa ningún backend de
ejecución ni cliente de red, y siempre termina diciendo que la decisión es manual y requeriría una fase nueva.

**Funciones y clases clave.**
- `build_report()` — orden **estructural**: (1) `compute_status` + `is_outcome_reveal_allowed`; si es falso solo dice qué
  falta y no consulta `trades` ni ningún resultado; (2) lee el registro oficial y, si no dice `APORTA_VALOR`, concluye «no
  pasar a dinero real»; (3) solo entonces evalúa los criterios del brazo ejecutado (`decision_source = LLM_APROBADA`).
- `evaluate_criteria()` — cada criterio es `PASA` / `NO PASA` / `SIN DATOS`; la falta de datos nunca cuenta como «pasa».
  Rentabilidad: PF > 1,3 **y** límite inferior del IC 95 % del PF > 1,0 (bootstrap por conglomerados).
- `compute_stressed()`, `compute_availability()`, `classify_executability()`, `compute_arm_log_summary()`, `recommend()`.

**Invariantes — no romper.** El reporte **cita** el veredicto del registro oficial, nunca lo recalcula. Los regímenes <30 /
30-100 / ≥100 por lado de la fila 3.8 original se retiraron: contradecían la regla de una sola mirada.

**Trampas conocidas.** `EXECUTED_ARM_DECISION_SOURCE` y las claves `real_arm_*` están repetidas aquí (no puede importar el
brazo por su guardia de imports); una prueba compara ambas copias.

**Protección.** Parcial: informe de solo lectura que se niega a mostrar resultados.

<!-- hechos:app/trading/real_money_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/real_money_report.html`](../api/app/trading/real_money_report.html)
- **Importa (internos):** `app.backtesting.metrics`, `app.config`, `app.persistence.models`, `app.persistence.repositories.trades_repo`, `app.trading.ai_value`, `app.trading.ai_value_report`, `app.trading.measurement_status`, `app.trading.shadow_live_labeler`, `app.trading.verdict_record`
- **Lo importan:** `scripts.real_money_report`
- **Configuración que lee:** `BACKTEST_CONCENTRATION_LIMIT_PCT`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `LLM_REAL_MAX_DELAY_SECONDS`, `MIN_CLOSED_TRADES`, `MIN_PAPER_TRADING_DAYS`, `MIN_PROFIT_FACTOR`, `real_account_eligible_strategies_list`
- **Tablas que lee:** `asset_universe`, `contract_specs_cache`, `llm_logs`, `ohlcv_cache`, `paper_execution_log`, `signals`, `system_state`, `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_real_money_report.py`
<!-- /hechos:app/trading/real_money_report.py -->

### `app/trading/reveal.py`

**Responsabilidad.** La puerta de los resultados de la cuenta paper para la API, el dashboard y Telegram.

**Funciones y clases clave.**
- `reveal_allowed()` — `is_outcome_reveal_allowed` pero **falla cerrado**: ante cualquier error al evaluarla, los
  resultados (PnL, precio de salida, motivo de cierre, saldo, equity) siguen ocultos.
- `HIDDEN_RESULTS` — el texto único que se muestra en su lugar.

**Invariantes — no romper.** Mientras la puerta esté cerrada solo se puede mostrar estructura (símbolo, lado, niveles, horas) y
conteos.

**Trampas conocidas.** Ninguna conocida.

**Protección.** Sí: [Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados).

<!-- hechos:app/trading/reveal.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/reveal.html`](../api/app/trading/reveal.html)
- **Importa (internos):** `app.core.logging`, `app.persistence.database`, `app.trading.measurement_status`
- **Lo importan:** `app.api.routes.account`, `app.api.routes.risk`, `app.telegram.push`, `scripts.resume_drawdown_stop`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_base.py`, `tests/unit/test_phase5_push.py`, `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/trading/reveal.py -->

### `app/trading/risk_engine.py`

**Responsabilidad.** El motor de riesgo **en vivo**: decide si una entrada **nueva** de la cuenta paper puede abrirse.
**Solo bloquea entradas nuevas**; nunca el cierre de una posición ni la ejecución de su SL/TP/liquidación.

**Funciones y clases clave.**
- `check_new_entry()` — devuelve una `RiskDecision` y audita cada rechazo en `risk_rejections`. Códigos:
  `KILL_SWITCH_ACTIVE`, `DRAWDOWN_STOP_ACTIVE` (solo modo `duro`), `CIRCUIT_BREAKER_ACTIVE`, `DAILY_LOSS_LIMIT`,
  `DATA_STALE`, `STRATEGY_NOT_ELIGIBLE`, `MAX_SIMULTANEOUS_POSITIONS`, `MAX_SAME_DIRECTION_POSITIONS`, `SL_CAP_EXCEEDED` y
  `MARGIN_UNAVAILABLE`.
- `update_equity_tracking()` y `record_trade_closed()` — pico de equity, línea base del día, racha de pérdidas y circuit
  breaker.
- `activate_kill_switch()`, `deactivate_kill_switch()`, `resume_drawdown_stop()` y los lectores `is_*`/`get_*`.

**Invariantes — no romper.**
- Todo el estado vive en `system_state` (`kill_switch_active`, `drawdown_stop_active`, `drawdown_breach_in_progress`,
  `drawdown_stop_would_have_triggered_count`, `equity_peak_usdt`, `consecutive_losses`, `circuit_breaker_until`,
  `daily_loss_day`, `daily_loss_baseline_usdt`, `daily_loss_limit_locked`, `drawdown_stop_last_resumed_at`): un reinicio lee
  exactamente el mismo estado y no «olvida» una pausa.
- La pérdida diaria y el drawdown se miden sobre **equity** (realizado + PnL flotante), que el llamador pasa; este módulo
  no toca la red. El «día» es el de `REPORT_TIMEZONE`.
- El stop por drawdown **nunca se levanta solo** (`resume_drawdown_stop`, manual); el contador del criterio de dinero real
  sube en cualquier modo.

**Trampas conocidas.**
- *Corregido:* `strategy=None` ya no salta el filtro de elegibilidad; solo lo salta una entrada marcada `is_manual=True`.
- *Corregido:* una entrada no manual sin `sl_margin_loss_pct` lanza `ValueError` (es un bug del llamador, no un rechazo).
- *Corregido:* el bloqueo por `DAILY_LOSS_LIMIT` queda **enganchado** hasta el cambio de día local; antes se recalculaba y
  una recuperación del PnL flotante el mismo día lo desactivaba sola.
- `DATA_STALE` usa el latido del WebSocket público (`ws_feed`), no el generador de señales.

**Protección.** No para la medición (afecta a la cuenta paper). Los umbrales son variables de configuración.

<!-- hechos:app/trading/risk_engine.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/risk_engine.html`](../api/app/trading/risk_engine.html)
- **Importa (internos):** `app.config`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.health_repo`, `app.persistence.repositories.risk_rejections_repo`, `app.persistence.repositories.system_state_repo`, `app.persistence.repositories.trades_repo`
- **Lo importan:** `app.api.routes.health`, `app.api.routes.risk`, `app.core.scheduler`, `app.execution.paper_backend`, `app.telegram.commands`, `app.telegram.push`, `app.trading.control_actions`, `app.trading.paper_execution`, `app.trading.position_monitor`, `scripts.resume_drawdown_stop`
- **Configuración que lee:** `CIRCUIT_BREAKER_COOLDOWN_HOURS`, `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`, `DRAWDOWN_STOP_MODE`, `INITIAL_CAPITAL_USDT`, `LIVE_SL_MARGIN_CAP_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_DRAWDOWN_PCT`, `MAX_SAME_DIRECTION_POSITIONS`, `MAX_SIMULTANEOUS_POSITIONS`, `REPORT_TIMEZONE`, `WS_STALE_AFTER_SECONDS`, `max_margin_for_new_trade`, `real_account_eligible_strategies_list`
- **Tablas que lee:** `data_source_health`, `system_state`, `trades`
- **Tablas que escribe:** `risk_rejections`, `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 14 archivos (e2e 2, unit 12)
<!-- /hechos:app/trading/risk_engine.py -->

### `app/trading/shadow_book.py`

**Responsabilidad.** El libro de **operaciones sombra**: una por señal agrupada, con margen ilimitado, para que la
comparación APROBADA vs RECHAZADA no dependa de los límites de riesgo ni de los cupos.

**Funciones y clases clave.**
- `ShadowBook.open_candidate()` — abre la sombra del candidato con la estrategia **representativa**: entra al **precio de
  cierre de la vela evaluada**, con el margen y el apalancamiento de la configuración, y los niveles de
  `resolve_trade_levels`; `INSERT OR IGNORE` por `signal_group_key`. Devuelve `None` si ya existía o si los niveles no valen.
- `signal_group_key()` — `símbolo|dirección|cierre de la vela`: **el hilo** que une una señal con su sombra
  (`shadow_trades`), su etiqueta (`llm_logs`) y su ejecución (`paper_execution_log`).
- `close()` — cierra liquidando el PnL real, idempotente; `close_unreliable()` — cierre administrativo **sin PnL**
  (`RECONCILE_FAILED`); `accrue_funding()`, `update_risk()` y `load_open()`.

**Invariantes — no romper.**
- Usa las **mismas funciones** que `PaperBackend` (`compute_open_fill`, `resolve_trade_levels`, `settle_close`): una
  señal tiene el mismo PnL en ambas cuentas.
- Dos diferencias deliberadas: **permite solapes** (una sombra nueva se abre aunque otra del mismo símbolo y dirección siga
  abierta) y entra al precio de cierre de la vela, sin demora ni slippage de llenado.
- `close_unreliable` es la única forma de cerrar sin PnL; esas sombras se excluyen del veredicto.

**Trampas conocidas.** El PnL de la sombra no es comparable con el de la cuenta real (entra sin demora ni slippage de
llenado); sí lo es entre APROBADA y RECHAZADA, que entran igual.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/trading/shadow_book.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/shadow_book.html`](../api/app/trading/shadow_book.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.execution.pnl`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.shadow_repo`, `app.persistence.repositories.specs_repo`, `app.trading.fills`, `app.trading.funding_accrual`, `app.trading.levels`, `app.trading.signal_generator`, `app.trading.slippage`
- **Lo importan:** `app.core.scheduler`, `app.main`, `app.trading.position_monitor`
- **Configuración que lee:** `BACKTEST_SLIPPAGE_BPS`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `TAKER_FEE_PCT`
- **Tablas que lee:** `contract_specs_cache`, `shadow_trades`
- **Tablas que escribe:** `shadow_trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_scheduler.py`, `tests/unit/test_shadow_book.py`
<!-- /hechos:app/trading/shadow_book.py -->

### `app/trading/shadow_live_labeler.py`

**Responsabilidad.** El **etiquetado en vivo** de las sombras durante la medición: por cada sombra que abre el scheduler,
dispara una tarea en segundo plano que pide al LLM su decisión (`LlmDecisionService.decide_group`, `fase='MEDICION'`) con el
prompt congelado y la guarda una sola vez.

**Funciones y clases clave.**
- `ShadowLiveLabeler.maybe_label()` — fuego y olvido; no hace nada si `LLM_LIVE_LABELING_ENABLED` está apagado o si el hash no
  coincide en este ciclo.
- `ShadowLiveLabeler.note_cycle_start()` — comprueba el hash **una vez por ciclo** y deja el resultado en caché, con un
  WARNING en cada ciclo mientras haya desajuste.
- `ShadowLiveLabeler.frozen_hash_ok` — comparación en vivo (la usa `GET /health`).
- `_label_one()` — solo etiqueta si la sombra **sigue abierta** y `decision_delay_s <= MAX_LABELING_DELAY_SECONDS` (1.800 s)
  **en el momento en que la tarea corre**; después llama al gancho `on_decision` (el brazo ejecutado) con
  `_dispatch_decision`, que nunca lanza.
- `FROZEN_PROMPT_SHA256` y `build_decision_service()`; `shutdown()` cancela las tareas pendientes.

**Invariantes — no romper.**
- `FROZEN_PROMPT_SHA256` es el valor que el autor congeló el 2026-10-12 tras el piloto real de `signal_review_v4`. Un
  cambio de texto o de *features* en `app/llm/prompts.py` cambia `PROMPT_SHA256` y el etiquetado se detiene solo hasta que
  alguien actualice la constante **a mano**.
- Si el hash no coincide, el grupo queda `SIN_LLM` **sin fila nueva** en `llm_logs`; nunca se descarta una fila ya escrita.
- Nunca importa `PaperBackend` ni escribe `trades`: no abre ni cierra posiciones; solo avisa al gancho **después** de
  persistir la etiqueta.
- Los fallos (timeout, HTTP, JSON inválido, presupuesto) dejan `SIN_LLM`, **nunca** `RECHAZADA`.

**Trampas conocidas.** Un reinicio, o una cola larga detrás del semáforo, puede dejar pasar más tiempo del que parece al
encolar: por eso el retraso se mide al correr la tarea. Una etiqueta cancelada a medias al apagar deja la sombra en
`SIN_LLM`.

**Protección.** Sí: [Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición) y
[Prompt y features](../ZONAS_PROTEGIDAS.md#prompt-y-features).

<!-- hechos:app/trading/shadow_live_labeler.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/shadow_live_labeler.html`](../api/app/trading/shadow_live_labeler.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.llm.decision_service`, `app.llm.prompts`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.shadow_repo`
- **Lo importan:** 14 módulos (api 2, core 1, main 1, scripts 6, trading 4)
- **Configuración que lee:** `LLM_LIVE_LABELING_ENABLED`
- **Tablas que lee:** `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 15 archivos (e2e 3, unit 12)
<!-- /hechos:app/trading/shadow_live_labeler.py -->

### `app/trading/shadow_report.py`

**Responsabilidad.** El reporte de las sombras por estrategia (subfase 3.5). **Lee resultados (PnL)**, por eso solo corre tras
la puerta (`scripts/shadow_report.py` se niega mientras `is_outcome_reveal_allowed` sea falso).

**Funciones y clases clave.**
- `build_report()` — lee la base y construye el reporte; `summarize_by_strategy()` y `summarize_totals()`;
  `render_markdown()`.

**Invariantes — no romper.**
- Una operación agrupada se acredita a **cada** estrategia contribuyente con su resultado completo, y el reporte separa los
  grupos de **una sola** estrategia (comparables con el backtest) de los **mixtos** (no comparables, con aviso).
- Las sombras `RECONCILE_FAILED` (PnL 0 forzado) se separan **antes** de calcular tasas: mezclarlas diluiría el winrate y el
  PF sin ser una pérdida real.

**Trampas conocidas.** El total no suma las filas por estrategia (una mixta cuenta en cada contribuyente).

**Protección.** Parcial: informe de solo lectura que se niega a mostrar resultados.

<!-- hechos:app/trading/shadow_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/shadow_report.html`](../api/app/trading/shadow_report.html)
- **Importa (internos):** `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.llm_logs_repo`, `app.persistence.repositories.shadow_repo`, `app.trading.ai_value`
- **Lo importan:** `scripts.shadow_report`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `llm_logs`, `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_ai_value.py`, `tests/unit/test_shadow_book.py`
<!-- /hechos:app/trading/shadow_report.py -->

### `app/trading/signal_generator.py`

**Responsabilidad.** El generador de señales **en vivo**: en cada ciclo itera todo el universo vigente × todas las
estrategias del registro (en sus timeframes) con **el mismo código que el backtest** (`precompute` + `evaluate`).

**Funciones y clases clave.**
- `run_signal_generation_cycle()` — por símbolo: refresca funding y contrato, descarga velas faltantes, descarta la vela en
  formación y evalúa cada estrategia; devuelve los `SignalCandidate`. Con el universo obsoleto o vacío no genera nada.
- `_evaluate_one()` — evalúa **una** estrategia e inserta su fila en `signals` (HOLD incluido); calcula
  `sl_margin_loss_pct` con la fórmula del backtest; una señal sobre `LIVE_SL_MARGIN_CAP_PCT` queda `DISCARDED_SL_CAP` y se
  audita en `signals_discarded_by_sl_cap`; con vela atrasada queda `STALE_DATA`.
- `_group_actionable_signals()` — agrupa por (símbolo, dirección, vela); `sl_margin_loss_pct` del candidato es el **peor**
  de las contribuyentes.
- `pick_representative_strategy()` — la primera, en orden alfabético, que esté en `REAL_ACCOUNT_ELIGIBLE_STRATEGIES`; si
  ninguna o la lista está vacía, la primera alfabética. Fija los niveles de la sombra y de la operación ejecutada.
- `SignalCandidate` y `get_stale_data_count()`.

**Invariantes — no romper.**
- **Solo velas cerradas.** Una fila por (símbolo, estrategia, timeframe, vela) (`UNIQUE` + `INSERT OR IGNORE`): si la vela ya
  se evaluó, la señal se salta por completo y nunca se reprocesa.
- Las estrategias de funding no se evalúan con funding obsoleto: HOLD con `reason="FUNDING_STALE"`. Los datos posteriores
  al último evento real se marcan `funding_is_approximated=True` en vivo.
- Los errores se aíslan por símbolo y timeframe: nunca tumban el ciclo.
- Los niveles que viajan en el candidato son los **crudos** (sin completar con el respaldo); se completan al precio real de
  llenado.

**Trampas conocidas.** El historial pedido es de 300 velas (750 para `funding_contrarian_percentile_experimental`, que
necesita 720+). Un fallo de red al descargar no detiene el ciclo: se evalúa con la caché, y si la vela está atrasada queda
`STALE_DATA` y sube `system_state.signals_stale_data_count`.

**Protección.** Sí: [Agrupación de señales y ciclo del scheduler](../ZONAS_PROTEGIDAS.md#agrupación-de-señales-y-ciclo-del-scheduler)
y [Tope de SL](../ZONAS_PROTEGIDAS.md#tope-de-sl).

<!-- hechos:app/trading/signal_generator.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/signal_generator.html`](../api/app/trading/signal_generator.html)
- **Importa (internos):** 20 módulos (backtesting 1, config 1, core 1, market 5, persistence 8, strategies 2, trading 2)
- **Lo importan:** `app.core.scheduler`, `app.trading.paper_execution`, `app.trading.shadow_book`
- **Configuración que lee:** `CANDLE_STALE_TOLERANCE_SECONDS`, `FUNDING_STALE_MARGIN_HOURS`, `LEVERAGE`, `LIVE_SL_MARGIN_CAP_PCT`, `UNIVERSE_STALENESS_HOURS`, `real_account_eligible_strategies_list`
- **Tablas que lee:** `asset_universe`, `contract_specs_cache`, `funding_cache`, `ohlcv_cache`, `system_state`
- **Tablas que escribe:** `signals`, `signals_discarded_by_sl_cap`, `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/series.py`, `tests/e2e/world.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_position_monitor.py`, `tests/unit/test_scheduler.py`, `tests/unit/test_shadow_book.py`, `tests/unit/test_signal_generator.py`, `tests/unit/test_universe_refresh.py`
<!-- /hechos:app/trading/signal_generator.py -->

### `app/trading/sl_calc.py`

**Responsabilidad.** El SL/TP de respaldo y el riesgo planeado (porcentaje del **margen**), extraídos de
`app/backtesting/engine.py` para que el generador en vivo use **exactamente** la misma fórmula.

**Funciones y clases clave.**
- `fallback_sl_tp_prices()` — SL y TP porcentuales (`BACKTEST_FALLBACK_SL_PCT` 5 %, `BACKTEST_FALLBACK_TP_PCT` 10 %, fracción
  del **precio**).
- `margin_loss_pct()` — `(distancia al SL / precio de entrada) × leverage × 100`, porcentaje 0-100 del margen.

**Invariantes — no romper.** Extracción pura: el backtest sigue dando los mismos números.

**Trampas conocidas.** `margin_loss_pct` **redondea a 9 decimales** a propósito: el SL de respaldo (5 % a 10x) cae
exactamente en el tope de 50 % y, sin redondear, la coma flotante daba `50.00000000000001` en ~77 % de los precios y la
comparación `>` descartaba la señal al azar según el precio.

**Protección.** Sí: [Tope de SL](../ZONAS_PROTEGIDAS.md#tope-de-sl) y [Niveles de SL y TP](../ZONAS_PROTEGIDAS.md#niveles-de-sl-y-tp).

<!-- hechos:app/trading/sl_calc.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/sl_calc.html`](../api/app/trading/sl_calc.html)
- **Importa (internos):** `app.config`, `app.strategies.base`
- **Lo importan:** `app.backtesting.engine`, `app.trading.levels`, `app.trading.signal_generator`
- **Configuración que lee:** `BACKTEST_FALLBACK_SL_PCT`, `BACKTEST_FALLBACK_TP_PCT`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_sl_calc.py`
<!-- /hechos:app/trading/sl_calc.py -->

### `app/trading/slippage.py`

**Responsabilidad.** El slippage como **costo plano en USDT**, con la misma fórmula que el backtest y el paper en vivo. No
mueve el precio: se resta del PnL neto como las comisiones.

**Funciones y clases clave.**
- `entry_slippage_usdt()` (`nocional × bps / 10000`) y `exit_slippage_usdt()` (`qty × precio de salida × bps / 10000`).

**Invariantes — no romper.** El parámetro es `BACKTEST_SLIPPAGE_BPS`, el mismo del backtest: una estrategia paga el mismo costo en
ambos.

**Trampas conocidas.** Ninguna conocida.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/trading/slippage.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/slippage.html`](../api/app/trading/slippage.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.backtesting.engine`, `app.execution.paper_backend`, `app.trading.fills`, `app.trading.shadow_book`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_slippage_and_funding_accrual.py`
<!-- /hechos:app/trading/slippage.py -->

### `app/trading/stop_engine.py`

**Responsabilidad.** El motor **compartido** de SL, TP y liquidación: modo VELA (backtest) y modo TICK (paper y sombras en
vivo).

**Funciones y clases clave.**
- Modo vela (extraídas sin cambios de `app/backtesting/engine.py`): `order_adverse_thresholds()`, `check_adverse_bar()` y
  `check_favorable_tp_bar()`.
- Modo tick: `check_adverse_tick()`, `check_favorable_tp_tick()` y `tp_tick_fill_price()`.
- `advance_trailing_stop()` — avanza el trailing con el extremo favorable de un tick o de una vela.

**Invariantes — no romper.**
- El modo tick devuelve siempre el **precio real observado** en el tick que disparó la condición, no el umbral nominal: cada
  disparo es, en los hechos, como un gap del modo vela.
- `order_adverse_thresholds` es común a ambos modos: qué umbral (SL o liquidación) queda más cerca del precio de entrada es
  una propiedad de los umbrales, no de cómo se observa el precio.

**Trampas conocidas.** Los tests del backtest no se tocaron al extraer las funciones: confirman que no hubo regresión.

**Protección.** Sí: [Sombras](../ZONAS_PROTEGIDAS.md#sombras).

<!-- hechos:app/trading/stop_engine.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/stop_engine.html`](../api/app/trading/stop_engine.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.backtesting.engine`, `app.trading.position_monitor`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_stop_engine.py`, `tests/unit/test_stop_engine_tp_fill.py`, `tests/unit/test_trailing_stop.py`
<!-- /hechos:app/trading/stop_engine.py -->

### `app/trading/verdict_record.py`

**Responsabilidad.** El **registro oficial del veredicto**: el archivo `docs/VEREDICTO_3_6_REGISTRO*.txt` que escribe
`scripts/ai_value_report.py --out`. Su **existencia** con un veredicto válido es lo que, junto con el gate abierto, permite
mostrar resultados.

**Funciones y clases clave.**
- `find_verdict_record()` — el primer registro con un veredicto válido; `parse_verdict_record()` y `load_official_record()`
  lo leen; `RECORD_GLOB`, `VALID_VERDICTS` (`APORTA_VALOR`, `NO_APORTA_VALOR`, `INCONCLUSO`) y `MAX_RECORD_BYTES`.

**Invariantes — no romper.**
- **Falla cerrado:** un archivo ausente, vacío, ilegible, enorme, sin la línea `Veredicto: <X>` o con otro texto, **no cuenta**.
- El placebo (`docs/PLACEBO_<fecha>.txt`) tiene un nombre que no cumple el patrón, a propósito.
- **No crear a mano archivos con ese nombre:** abrirían la puerta de los resultados sin veredicto.

**Trampas conocidas.** `docs/` se monta en el contenedor de solo lectura y el bot lo consulta en cada petición.

**Protección.** Sí: [Gate, placebo y veredicto](../ZONAS_PROTEGIDAS.md#gate-placebo-y-veredicto).

<!-- hechos:app/trading/verdict_record.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/trading/verdict_record.html`](../api/app/trading/verdict_record.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.trading.measurement_status`, `app.trading.real_money_report`, `scripts.ai_value_report`, `scripts.real_money_report`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_no_leaks.py`, `tests/unit/test_ai_value_report_script.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_outcome_reveal.py`, `tests/unit/test_readonly_api_routes.py`, `tests/unit/test_real_money_report.py`, `tests/unit/test_real_money_report_script.py`, `tests/unit/test_shadow_report_script.py`
<!-- /hechos:app/trading/verdict_record.py -->
