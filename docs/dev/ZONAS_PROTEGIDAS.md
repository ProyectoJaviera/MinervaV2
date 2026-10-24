# Zonas protegidas de la medición

Este documento dice qué partes del código, de la configuración y de la base **no se pueden
cambiar mientras la medición está en curso** porque la invalidarían, por qué, y qué hacer si de
verdad hay que cambiar una. Es el documento más importante de `docs/dev/`: léelo antes de tocar
cualquier cosa que no sea documentación.

La lista de zonas vive en un solo lugar, `ZONES` en `scripts/check_measurement_safety.py`, y una
prueba (`tests/unit/test_check_measurement_safety.py`) comprueba que este documento explica cada
zona con todos sus archivos y variables. Si cambias una, cambia la otra.

## Qué se está midiendo y por qué cualquier cambio importa

**La pregunta.** ¿El filtro del LLM aporta valor? Cada señal agrupada (símbolo, dirección y vela
cerrada) abre una **sombra**: una operación simulada con margen ilimitado. El LLM la etiqueta
`APROBADA` o `RECHAZADA` (o `SIN_LLM` si falla). La medición compara el retorno sobre el margen de
unas y otras con un bootstrap por conglomerados y entrega **un único veredicto**:
`APORTA_VALOR`, `NO_APORTA_VALOR` o `INCONCLUSO`. El diseño completo está en
[`FASE3_6_LLM.md`](../FASE3_6_LLM.md) (secciones i y m) y el procedimiento en
[`VEREDICTO_3_6.md`](../VEREDICTO_3_6.md).

**Qué está fijado de antemano** (constantes de `app/trading/ai_value.py`, decididas antes de ver
datos):

| Constante | Valor | Qué fija |
|---|---|---|
| `MIN_EFFECTIVE_N` | 300 | conglomerados efectivos por lado para llegar al punto de análisis |
| `DELTA_PCT` | 0.03 | margen de no-valor, como fracción del margen por operación |
| `MAX_SIN_LLM_SHARE` | 0.10 | tope de grupos `SIN_LLM` antes de declarar `INCONCLUSO` |
| `BOOTSTRAP_RESAMPLES` y `BOOTSTRAP_SEED` | 2000 y 20261004 | remuestreos del bootstrap |
| `PLACEBO_REPEATS`, `PLACEBO_SEED`, `PLACEBO_CALIBRATION_THRESHOLD` | 1000, 20261009, 0.05 | calibración por placebo |

**La identidad congelada** (verificable con `scripts/measurement_identity.py` sobre una copia):

- modelo `claude-sonnet-5-5` (variable `ANTHROPIC_SONNET_MODEL`);
- prompt `signal_review_v4` y features `features_v2`;
- hash combinado `0fa0dd75a3fecdae36f3b77c8b7ec5d97d58a2238349283c61bfa7632188c777`
  (`FROZEN_PROMPT_SHA256`).

**Qué hace válida la medición.** Cuatro propiedades; cada zona de abajo protege una de ellas:

| Propiedad | Si se rompe | Zonas |
|---|---|---|
| 1. El modelo recibe siempre lo mismo y responde en las mismas condiciones | APROBADA y RECHAZADA dejan de ser comparables | Prompt y features, Etiquetador y medición |
| 2. Las oportunidades nacen de reglas fijas | cambia la población que se mide | Estrategias y parámetros, Universo, Agrupación de señales y ciclo del scheduler, Tope de SL |
| 3. El resultado de cada oportunidad se calcula con la misma fórmula | el retorno de unas filas no es el de otras | Niveles de SL y TP, Sombras |
| 4. Nadie mira resultados antes del punto de análisis único | el veredicto se puede sesgar mirando y repitiendo | Gate, placebo y veredicto, Ocultamiento de resultados, Parámetros de configuración protegidos |

Además hay dos zonas de **identidad de los datos** (Claves de la medición y Esquema de las tablas
de la medición): si las claves o el esquema cambian, las filas viejas y las nuevas dejan de
significar lo mismo.

## Cómo comprobarlo: `scripts/check_measurement_safety.py`

El script compara el árbol de trabajo (o el índice, o los commits desde una revisión) contra la
lista de zonas y dice con claridad qué archivos protegidos cambiaron y de qué manera. **Solo lee**:
ejecuta `git` y lee archivos del repositorio; no importa `app`, no lee `.env`, no abre la base y
no escribe nada (una prueba lo comprueba).

```
python scripts/check_measurement_safety.py                  # árbol de trabajo contra HEAD
python scripts/check_measurement_safety.py --staged         # solo lo que entraría al commit
python scripts/check_measurement_safety.py --since <commit> # todo lo hecho desde ese commit
python scripts/check_measurement_safety.py --since-measurement
python scripts/check_measurement_safety.py --list           # imprime las zonas y sale
```

`--since-measurement` compara con `c13f8ca`, el commit que cierra el congelamiento del 2026-10-12
(prompt v4, etiquetador en vivo y su registro). Añade `--strict` para que las zonas a revisar
también hagan fallar el comando.

**Códigos de salida:** `0` sin cambios protegidos (o solo de documentación); `1` hay cambios
protegidos (con `--strict`, también si hay algo para revisar); `2` error de uso o de git.

**Cómo clasifica cada cambio:**

| Etiqueta | Qué significa | Nivel |
|---|---|---|
| `codigo cambiado` o `archivo cambiado` | el contenido cambió más allá de docstrings, comentarios y espacios | PROTEGIDO o REVISAR según la zona |
| `fijado por hash` | cambió un byte de `app/llm/prompts.py` o `app/llm/market_features.py` (ni un comentario) | PROTEGIDO |
| `archivo nuevo` o `archivo borrado` | aparece o desaparece un archivo de una zona | según la zona |
| `parametro protegido` | cambió el valor por defecto de una variable protegida (en `app/config.py`, `.env.example`, `docker-compose.yml` o `scripts/start_bot.ps1`) | según la variable |
| `variable nueva en el archivo` | `docker-compose.yml` o `scripts/start_bot.ps1` ahora fijan una variable que antes no fijaban | REVISAR |
| `esquema de una tabla de la medicion` | cambió, se quitó o se redefinió algo existente en el DDL | PROTEGIDO |
| `columna nueva (aditivo)` | se añadió una columna a una tabla de la medición | REVISAR |
| `registro oficial del veredicto creado a mano` | apareció `docs/VEREDICTO_3_6_REGISTRO*.txt` | PROTEGIDO |
| `solo documentacion` | el AST es el mismo: cambian docstrings, comentarios o espacios | no bloquea |

**Lo que el script no puede ver** (no confíes en que "no avisó" significa "no pasó nada"):

- **El `.env` real, las variables de entorno del proceso y el contenedor que corre.** Solo ve el
  repositorio. El contenedor sigue con el código viejo hasta que se reconstruye la imagen y se
  recrea. Para el modelo configurado en el entorno usa `scripts/measurement_identity.py`.
- **La semántica.** Una función no protegida que una protegida llama, y que cambia su resultado,
  no se detecta. La lista de zonas se mantiene a mano y la prueba solo comprueba que cada patrón
  exista.
- **Los parámetros de la llamada al modelo** (`thinking`, `effort`): no están en ningún hash ni
  en `llm_logs`; ver [Etiquetador y medición](#etiquetador-y-medición).
- **Cambios de dependencias** más allá de avisar que `pyproject.toml` o `docker/requirements.lock`
  cambiaron.

### Gancho pre-commit opcional (no instalado)

Este repositorio **no instala ningún gancho** de git (en `.git/hooks/` solo hay los `.sample`). Si
quieres que git frene un commit que toca una zona protegida, créalo tú: el archivo
`.git/hooks/pre-commit` de dos líneas que llama a este script con `--staged` está en
[`ENTORNO_DESARROLLO.md`](ENTORNO_DESARROLLO.md#gancho-pre-commit-opcional-no-instalado), con la
manera de saltarlo cuando el cambio está revisado (`git commit --no-verify`).

## Mapa rápido

| Zona | Nivel | En una línea | Pruebas que la protegen |
|---|---|---|---|
| [Prompt y features](#prompt-y-features) | PROTEGIDO | el texto y las features que ve el modelo, fijados por hash | `tests/unit/test_prompt_v5_prepared.py`, `tests/unit/test_llm_prompts.py` |
| [Etiquetador y medición](#etiquetador-y-medición) | PROTEGIDO | cómo se llama al modelo y qué respuesta vale | `tests/unit/test_shadow_live_labeler.py`, `tests/unit/test_llm_decision_service.py` |
| [Estrategias y parámetros](#estrategias-y-parámetros) | PROTEGIDO | las seis estrategias y sus parámetros pre-registrados | `tests/unit/test_ema_cross_strategy.py` y las demás `*_strategy.py` |
| [Universo](#universo) | PROTEGIDO | qué símbolos se evalúan | `tests/unit/test_universe_refresh.py` |
| [Agrupación de señales y ciclo del scheduler](#agrupación-de-señales-y-ciclo-del-scheduler) | PROTEGIDO | velas cerradas, una fila por vela, una sombra por grupo | `tests/unit/test_signal_generator.py`, `tests/unit/test_scheduler.py` |
| [Niveles de SL y TP](#niveles-de-sl-y-tp) | PROTEGIDO | dónde se ponen SL, TP, trailing y liquidación | `tests/unit/test_sl_calc.py`, `tests/unit/test_liquidation.py` |
| [Tope de SL](#tope-de-sl) | PROTEGIDO | qué señales se descartan por arriesgar demasiado | `tests/unit/test_signal_generator.py` |
| [Sombras](#sombras) | PROTEGIDO | cómo se abre, se cierra y se calcula el PnL | `tests/unit/test_shadow_book.py`, `tests/unit/test_position_monitor.py` |
| [Claves de la medición](#claves-de-la-medición) | PROTEGIDO | qué filas pertenecen a la medición | `tests/unit/test_llm_logs_repo.py`, `tests/unit/test_signals_repo.py` |
| [Esquema de las tablas de la medición](#esquema-de-las-tablas-de-la-medición) | PROTEGIDO | el DDL de las tablas medidas y el disparador | `tests/unit/test_database_migrations.py` |
| [Gate, placebo y veredicto](#gate-placebo-y-veredicto) | PROTEGIDO | las reglas de decisión y el procedimiento de una sola mirada | `tests/unit/test_ai_value.py`, `tests/unit/test_ai_value_placebo.py`, `tests/unit/test_ai_value_report.py` |
| [Ocultamiento de resultados](#ocultamiento-de-resultados) | PROTEGIDO | nadie ve PnL antes del registro oficial | `tests/unit/test_outcome_reveal.py`, `tests/e2e/test_no_leaks.py` |
| [Parámetros de configuración protegidos](#parámetros-de-configuración-protegidos) | PROTEGIDO | los valores de las variables protegidas, donde estén escritos | `tests/unit/test_env_example.py` |
| [Zonas para revisar](#zonas-para-revisar) | REVISAR | nueve zonas que no invalidan por sí solas pero pueden estropear la medición | varias |

## Prompt y features

**Qué cubre:** `app/llm/prompts.py` y `app/llm/market_features.py` (los dos **fijados por hash**:
ni un byte puede cambiar, tampoco un comentario) y `tests/unit/test_prompt_v5_prepared.py` (la
prueba que los fija).

**Por qué.** La medición compara lo que el modelo aprueba con lo que rechaza *bajo el mismo
prompt*. Si el texto, una feature o su unidad cambian, las filas anteriores y posteriores no son
comparables. El caso que lo enseñó: el piloto v2 encontró que `volatility_atr14_pct` salía como
fracción cruda en vez de porcentaje; el texto del prompt no había cambiado, así que ningún hash lo
habría notado. Por eso `PROMPT_SHA256` cubre dos cosas:

```
PROMPT_SHA256 = sha256(SYSTEM_PROMPT + "\n---FEATURES_VERSION---\n" + FEATURES_VERSION)
```

y `FEATURES_VERSION` hay que subirla ante *cualquier* cambio de campos o unidades del mensaje,
aunque `SYSTEM_PROMPT` no cambie.

**Cómo se protege hoy** (tres capas independientes):

1. **En ejecución.** Cada ciclo del scheduler, `ShadowLiveLabeler.note_cycle_start` compara
   `PROMPT_SHA256` con `FROZEN_PROMPT_SHA256`. Si no coinciden, el etiquetado de ese ciclo no
   corre (los grupos quedan `SIN_LLM` sin fila nueva en `llm_logs`) y hay un WARNING por ciclo.
   `GET /health` lo expone como `llm_labeling.frozen_hash_ok`.
2. **En las pruebas.** `tests/unit/test_prompt_v5_prepared.py` fija el SHA-256 de los dos archivos
   (con los saltos de línea normalizados a LF), el hash de `SYSTEM_PROMPT`, `PROMPT_VERSION`,
   `FEATURES_VERSION` y el formato exacto de `build_user_message`. Su mensaje de error dice qué
   hacer: si el cambio es deliberado, *después del veredicto y con aprobación*, se actualiza la
   huella a mano.
3. **En los datos.** Cada fila de `llm_logs` guarda `prompt_sha256`, `prompt_version` y
   `features_version`, y `measurement_keys` solo toma un hash (ver
   [Claves de la medición](#claves-de-la-medición)).

**Trampas.**

- `prompts.py` lee `FUNDING_STALE_MARGIN_HOURS` (lo pasa a `build_market_features`): cambiar su
  valor por defecto cambia cuándo el funding le llega al modelo como `null`. Es una variable
  protegida (ver [Parámetros de configuración protegidos](#parámetros-de-configuración-protegidos)).
- Añadir una feature nueva, aunque "no cambie nada", es un cambio de `FEATURES_VERSION` y, por lo
  tanto, del hash: es una medición nueva.
- `app/llm/prompts_v5.py` está preparado y sin uso. Nada puede importarlo (ver
  [Prompt v5 preparado](#prompt-v5-preparado)).
- Por estar fijados por hash, estos dos archivos son la única excepción de la regla de docstrings
  completos (`tests/unit/test_docstrings.py` los exime).

## Etiquetador y medición

**Qué cubre:** `app/trading/shadow_live_labeler.py`, `app/llm/decision_service.py`,
`app/llm/client.py`, `app/llm/_anthropic_client.py`, `app/llm/schemas.py` y `app/llm/budget.py`.
Variables: `ANTHROPIC_SONNET_MODEL`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS`,
`LLM_MAX_CONCURRENCY` y `LLM_LIVE_LABELING_ENABLED`.

**Por qué.** Este código decide **qué filas existen** en `llm_logs` y qué etiqueta tiene cada
sombra. Las reglas pre-registradas que viven aquí:

- **Una sola llamada por grupo, sin reintentos.** `signal_group_key` es UNIQUE en `llm_logs`.
- **Todo fallo es `SIN_LLM`, nunca `RECHAZADA`** (timeout, error HTTP, JSON inválido, presupuesto
  agotado). Un fallo que se contara como rechazo sesgaría la comparación.
- **La etiqueta se escribe una sola vez** (`shadow_repo.set_llm_decision` y el disparador
  `llm_decision_inmutable`).
- **Solo se etiqueta una sombra que sigue abierta** y cuyo retraso de decisión es de a lo sumo
  `MAX_LABELING_DELAY_SECONDS` (1800 s), comprobado cuando la tarea de verdad corre, no cuando se
  encola.
- **La llamada** (`_anthropic_client.py`): `thinking = {"type": "between_tools"}`,
  `output_config = {"effort": "low"}`, sin `temperature`, `max_tokens` = `LLM_MAX_TOKENS` (300),
  tiempo máximo `LLM_TIMEOUT_SECONDS` (20 s) y concurrencia `LLM_MAX_CONCURRENCY` (4).
- **La validación** (`schemas.py`): `extra="forbid"`; un `razonamiento` largo se *trunca* en vez de
  invalidar la decisión; hay un único reparo acotado (comilla de cierre del `razonamiento`
  omitida). Cambiar cualquiera de estas reglas cambia cuántas filas son `INVALID`, es decir, cuántos
  grupos terminan `SIN_LLM`.
- **El presupuesto** (`budget.py`): se reserva el costo máximo bajo un candado antes de llamar; el
  "día" del presupuesto es la medianoche local de `REPORT_TIMEZONE`.

**Qué pasa si se toca.** Cambia el conjunto de filas o su significado; el hash no lo detecta.

**Trampas.**

- **El modelo y los parámetros de la llamada NO están en el hash.** `FROZEN_PROMPT_SHA256` cubre
  el texto y las features, no `ANTHROPIC_SONNET_MODEL`, y menos `thinking` y `effort`. Un cambio de
  modelo se haría efectivo en el próximo reinicio y el etiquetado seguiría "sano". Solo
  `scripts/measurement_identity.py` lo detecta (cuenta las filas de `MEDICION` por modelo, prompt,
  hash y versión de features). `thinking` y `effort` no se registran en ninguna parte: el único
  control es no tocar `_anthropic_client.py`.
- `real_open_allowed` vive en `decision_service.py` pero **pertenece al brazo ejecutado** de la
  cuenta paper: editarlo no cambia lo medido, aunque el archivo esté protegido.
- `LLM_LIVE_LABELING_ENABLED` vale `false` por defecto en `app/config.py`; el bot real lo activa
  (`scripts/start_bot.ps1` y `docker-compose.yml`). Apagarlo no invalida lo ya medido, pero detiene
  la medición: las sombras siguen naciendo `SIN_LLM` y el tope `MAX_SIN_LLM_SHARE` se acerca.
- Los parámetros operativos del LLM (presupuesto y precios) están en
  [Parámetros operativos](#parámetros-operativos).

## Estrategias y parámetros

**Qué cubre:** `app/strategies/*.py` (el registro, la clase base y las seis estrategias),
`app/indicators/engine.py` y `app/backtesting/funding.py`. Variable:
`REAL_ACCOUNT_ELIGIBLE_STRATEGIES`.

**Por qué.** Las estrategias son la fuente de las señales; el LLM las revisa, no las inventa. Sus
parámetros están pre-registrados (Fase 2) y definen qué señales nacen y cuándo:

| Estrategia | Timeframes |
|---|---|
| `ema_cross_9_21` | 4h |
| `trend_atr_stop_9_21_50` | 4h |
| `mean_reversion_rsi14_bb20` | 1h |
| `donchian_breakout_20` | 4h y 1d |
| `funding_contrarian_experimental` | 4h |
| `funding_contrarian_percentile_experimental` | 4h |

Agregar, quitar, renombrar o cambiar el timeframe de una estrategia cambia la población medida.

**Trampas.**

- **`REAL_ACCOUNT_ELIGIBLE_STRATEGIES` no es solo de la cuenta paper.** Su nombre sugiere que solo
  decide qué estrategias pueden abrir en la cuenta, pero `pick_representative_strategy`
  (`app/trading/signal_generator.py`) lo usa para elegir la **estrategia representante** de un
  grupo: la primera, en orden alfabético, que esté en la lista; si ninguna lo está, la primera
  alfabética. La representante fija los niveles de la sombra (SL, TP, trailing) y el campo
  `strategy`. Cambiar la lista cambia las sombras.
- `app/indicators/engine.py` (`ema`, `atr`, `rsi`, `bollinger_bands`, `donchian_channel`) lo
  comparten las estrategias y el backtest. `app/backtesting/funding.py` construye las columnas de
  funding que leen las dos estrategias `funding_contrarian_*`; `signal_generator.py` lo importa
  aunque viva en `backtesting/`.
- Hay un cambio que parece inofensivo y no lo es: ajustar un umbral "para que genere más señales".

## Universo

**Qué cubre:** `app/market/universe.py`, `app/market/coingecko_client.py` y
`app/persistence/repositories/universe_repo.py`. Variables: `UNIVERSE_SIZE`,
`UNIVERSE_CANDIDATE_POOL`, `UNIVERSE_EXCLUDE_CATEGORIES`, `UNIVERSE_MANUAL_EXCLUSIONS`,
`UNIVERSE_PRICE_SANITY_TOLERANCE_PCT`, `UNIVERSE_REFRESH_HOURS` y `UNIVERSE_STALENESS_HOURS`.

**Por qué.** El universo decide **qué símbolos producen oportunidades**. La regla
(`build_universe_snapshot`): los `UNIVERSE_CANDIDATE_POOL` primeros de CoinGecko por
capitalización, menos las categorías excluidas (stablecoins, wrapped, liquid staking) y las
exclusiones manuales, intersectados con los perpetuos USDT con `symbolStatus = OPEN` de Bitunix,
con un chequeo de sanidad de precio entre ambas fuentes, hasta `UNIVERSE_SIZE` símbolos. Los
conglomerados de la medición son por símbolo y dirección: cambiar la regla a mitad cambia la
población.

**Trampas.**

- **El universo cambia solo, y eso es correcto.** La capitalización se mueve y el snapshot se
  refresca cada `UNIVERSE_REFRESH_HOURS`; lo que está protegido es la *regla*, no los diez
  símbolos. Cada cambio queda auditado en `asset_universe` y se avisa por Telegram.
- Si el refresco falla se conserva el último snapshot; pasadas `UNIVERSE_STALENESS_HOURS` el
  universo se considera obsoleto y `signal_generator` deja de evaluar. Cambiar ese umbral cambia
  cuándo se detiene la medición.
- La atribución "Data provided by CoinGecko" es obligatoria en el dashboard (restricción de costos
  y términos del proyecto).

## Agrupación de señales y ciclo del scheduler

**Qué cubre:** `app/trading/signal_generator.py`, `app/core/scheduler.py`,
`app/market/ohlcv_history.py` y `app/market/funding_history.py`. Variables:
`SCHEDULER_POLL_SECONDS`, `CANDLE_STALE_TOLERANCE_SECONDS` y `FUNDING_STALE_MARGIN_HOURS`.

**Por qué.** Define la **unidad de observación**:

- Solo se evalúan **velas cerradas** (`drop_incomplete_last_bar` descarta la que se está formando).
- Una fila en `signals` por (símbolo, estrategia, timeframe, vela), con `UNIQUE` e
  `INSERT OR IGNORE`: una vela ya evaluada no se reprocesa jamás.
- Una vela atrasada marca la fila `STALE_DATA` y la señal no se agrupa; una estrategia de funding
  sin funding fresco queda `HOLD` con `FUNDING_STALE`.
- Las señales accionables que sobreviven al [tope de SL](#tope-de-sl) se agrupan por (símbolo,
  dirección, vela) en un `SignalCandidate`: si dos estrategias coinciden en el mismo lado de la
  misma vela cuentan como **una** sola oportunidad, con `contributing_strategies`.
- Cada grupo abre **una sombra** (`ShadowBook.open_candidate`) y dispara el etiquetado en segundo
  plano.

Cambiar cualquiera de estas reglas cambia cuántas observaciones hay y cuáles se agrupan, es
decir, el N efectivo.

**Trampas.**

- `SCHEDULER_POLL_SECONDS` (60 s) fija cuánto tarda el bot en notar un cierre de vela: afecta el
  retraso de decisión (`decision_delay_s`) que registra `llm_logs`.
- **Pausar el scheduler detiene la medición** (sin señales, sin sombras y sin etiquetado), y la
  pausa sobrevive a un reinicio a propósito. Con la pausa, `last_cycle_at` no avanza para que
  `GET /health` pase a `stale`. Ver [Controles que detienen la medición](#controles-que-detienen-la-medición).
- `FUNDING_STALE_MARGIN_HOURS` también llega al modelo: `app/llm/prompts.py` se lo pasa a
  `build_market_features`.
- La apertura en la cuenta paper sin LLM (`AUTO_OPEN_WITHOUT_LLM`) está apagada y debe seguir así;
  ver [Cuenta paper y brazo ejecutado](#cuenta-paper-y-brazo-ejecutado).

## Niveles de SL y TP

**Qué cubre:** `app/trading/levels.py`, `app/trading/sl_calc.py` y
`app/backtesting/liquidation.py`. Variables: `BACKTEST_FALLBACK_SL_PCT`,
`BACKTEST_FALLBACK_TP_PCT` y `LEVERAGE`.

**Por qué.** `resolve_trade_levels` decide dónde se ponen el SL, el TP, el trailing y la
liquidación de cada sombra, y es lo que determina cómo termina:

- Los niveles absolutos que devuelve la estrategia se respetan. Si falta el SL se usa el de
  respaldo (`BACKTEST_FALLBACK_SL_PCT`, 5 %); si faltan el TP y el trailing se usa el TP de respaldo
  (`BACKTEST_FALLBACK_TP_PCT`, 10 %). Son **fracciones 0-1 de movimiento de precio**, no de margen.
- Los niveles se calculan al precio real de llenado (para una sombra, el precio de cierre de la
  vela evaluada), nunca al precio de la señal.
- La liquidación (`compute_liquidation_price`) usa el nocional para elegir el tramo de margen de
  mantenimiento del contrato.
- Si el SL ya fue superado al llenar, la sombra se descarta ("niveles inválidos").

**Trampas.**

- **`BACKTEST_*` no significa "solo backtest".** Estos SL y TP de respaldo se usan en vivo.
- **Es código compartido.** `sl_calc.py` (que se extrajo del backtest para que el generador en vivo use
  la misma fórmula) y `liquidation.py` los usa también el motor del backtest, y `levels.py` lo usan las
  sombras y la cuenta paper: cambiarlos cambia el resultado de las tres y, en el caso del backtest, los
  números que fijan sus pruebas de reproducibilidad.
- El riesgo planeado (`margin_loss_pct`) está en [Tope de SL](#tope-de-sl).

## Tope de SL

**Qué cubre:** `app/trading/signal_generator.py` (donde se aplica el tope). Variable:
`LIVE_SL_MARGIN_CAP_PCT`.

**Por qué.** Decide **qué señales entran en la medición**: una señal accionable cuyo
`sl_margin_loss_pct` supera `LIVE_SL_MARGIN_CAP_PCT` (50.0) queda `DISCARDED_SL_CAP`, se audita en
`signals_discarded_by_sl_cap` y **nunca** llega a ser sombra ni a verse por el LLM.

**Trampas.**

- **Escala: porcentaje 0-100**, no fracción (`50.0` es el 50 %, no `0.5`). `app/config.py` lo
  valida al arrancar (`_validate_pct_units`) y falla fuerte si está fuera de escala.
- **El SL de respaldo cae exactamente en el tope.** Un SL del 5 % a 10x arriesga el 50 % del margen,
  justo el valor del tope. `margin_loss_pct` (en `app/trading/sl_calc.py`) redondea a 9 decimales
  precisamente por eso: sin redondeo, la coma flotante daba `50.00000000000001` en ~77 % de los
  precios y la comparación `>` descartaba señales al azar según el precio. Cambiar el redondeo o
  `>` por `>=` cambia qué señales entran.
- **La misma variable gobierna la cuenta paper**: el motor de riesgo (`risk_engine.check_new_entry`)
  rechaza con el mismo tope. Cambiarla afecta a las dos cuentas; lo medido es la parte del
  generador.

## Sombras

**Qué cubre:** `app/trading/shadow_book.py`, `app/trading/fills.py`, `app/trading/slippage.py`,
`app/trading/funding_accrual.py`, `app/trading/stop_engine.py`, `app/trading/position_monitor.py` y
`app/execution/pnl.py`. Variables: `DEFAULT_MARGIN_USDT`, `TAKER_FEE_PCT`, `BACKTEST_SLIPPAGE_BPS`
y `TP_GAP_TOLERANCE_PCT`.

**Por qué.** El PnL de las sombras **es el resultado que se mide**. Reglas que no se pueden mover:

- **Apertura** (`ShadowBook.open_candidate`): precio de cierre de la vela evaluada de la estrategia
  representante, `opened_at` = cierre de la vela, margen `DEFAULT_MARGIN_USDT`, comisión de entrada
  `TAKER_FEE_PCT` y slippage plano `BACKTEST_SLIPPAGE_BPS`; **margen ilimitado y solapes
  permitidos** (una RECHAZADA no bloquea a la siguiente).
- **Cierre**: solo lo hace `PositionMonitor`. SL y trailing en modo tick llenan al precio observado
  (peor caso); el TP llena al nominal salvo hueco evidente (`TP_GAP_TOLERANCE_PCT`); la liquidación
  exige que el mark por REST la confirme; la reconciliación con velas de 1 minuto llena al precio
  nominal (`CANDLE_RECON`). Todo cierre registra su `fill_source`.
- **PnL** (`settle_close`): resta comisión de entrada y salida, slippage de entrada y salida y el
  funding acumulado (`compute_funding_accrual`, que no cuenta ningún evento dos veces).
- **Misma fórmula en las dos cuentas.** `ShadowBook` y `PaperBackend` comparten `compute_open_fill`,
  `resolve_trade_levels` y `settle_close` para que una señal tenga el mismo PnL en ambas.

**Trampas.**

- **`DEFAULT_MARGIN_USDT` y `LEVERAGE`.** El veredicto mide el retorno como fracción del margen
  (`pnl_net_usdt / margin_usdt`), de modo que, por diseño, mezclar operaciones de antes y de después
  de un cambio de margen no rompe la comparación (`app/trading/ai_value.py` lo dice así). Aun así
  están protegidas: las comisiones, el slippage y el funding son proporcionales al nocional, que
  además elige el tramo de margen de mantenimiento de la liquidación, y el apalancamiento fija la
  distancia a la liquidación y el riesgo planeado del [tope de SL](#tope-de-sl). Cuánto cambiaría el
  veredicto no está medido; la recomendación del proyecto es cambiarlos solo al empezar una medición
  nueva ([`../CONFIGURACION.md`](../CONFIGURACION.md#qué-no-cambiar-durante-una-medición)).
- Las sombras cuya reconciliación falló (`RECONCILE_FAILED`, sin PnL fiable) se **excluyen** del
  veredicto (`get_unreliable_signal_group_keys`). Cambiar cuándo se rinde la reconciliación cambia
  cuántas filas se excluyen.
- Los tiempos del monitor (`MONITOR_TICK_SECONDS`, `TICK_STALE_SECONDS`, `WS_STALE_AFTER_SECONDS`)
  están en [Parámetros operativos](#parámetros-operativos).
- El monitor también cierra las posiciones de la cuenta paper: un cambio en `position_monitor.py`
  afecta a las dos.

## Claves de la medición

**Qué cubre:** `app/persistence/repositories/shadow_repo.py`,
`app/persistence/repositories/llm_logs_repo.py` y `app/persistence/repositories/signals_repo.py`.

**Por qué.** Las claves y los filtros deciden **qué filas pertenecen a la medición**:

- `signal_group_key` = `símbolo|dirección|cierre de la vela` (`shadow_book.signal_group_key`). Es
  `UNIQUE` en `shadow_trades`, `llm_logs` y `paper_execution_log`: el hilo que une una señal con su
  sombra, su etiqueta y su ejecución.
- `signals` es `UNIQUE (symbol, strategy, timeframe, candle_close_time)`.
- **`measurement_keys`** = `llm_logs_repo.get_measurement_signal_group_keys`: las claves con
  `fase = 'MEDICION'` y un **único** `prompt_sha256`, el de la fila más reciente si no se indica.
  Excluye el piloto y cualquier otra versión del prompt.
- El veredicto además excluye las sombras no confiables y el piloto.

**Trampas.**

- **"El hash de la fila más reciente".** Una sola fila `MEDICION` escrita con otro hash (una prueba
  que etiqueta contra la base equivocada, una corrida de desarrollo con el prompt modificado) pasa a
  ser "la más reciente" y **saca en silencio todas las filas anteriores** de `measurement_keys`. Por
  eso el etiquetador se niega a escribir con un hash distinto y por eso existe
  `scripts/measurement_identity.py`.
- Las escrituras idempotentes (`INSERT OR IGNORE`, `UPDATE ... WHERE llm_decision = 'SIN_LLM'`) son
  parte del contrato: cambiarlas permite duplicados o reescribir una etiqueta.

## Esquema de las tablas de la medición

**Qué cubre:** `app/persistence/database.py`, solo en lo que toca a `shadow_trades`, `llm_logs`,
`signals`, `signals_discarded_by_sl_cap`, `asset_universe` y el disparador
`llm_decision_inmutable` (el resto de las tablas no es una zona protegida).

**Por qué.** El esquema es el significado de las filas. Los `CHECK` fijan los valores válidos
(`llm_decision IN ('APROBADA', 'RECHAZADA', 'SIN_LLM')`, `side`, `status`...), los `UNIQUE` fijan la
identidad (`signal_group_key`) y el disparador impide reescribir una etiqueta ya decidida. Si
cambian, las filas viejas y las nuevas dejan de ser comparables.

**Qué sí se puede hacer.** **Agregar una columna** a una tabla existente es aditivo y seguro si es
nula o tiene valor por defecto y nada del camino medido la lee ni la escribe: se declara en el
diccionario `_<TABLA>_MIGRATED_COLUMNS` (declararla solo en el `CREATE TABLE` no la agrega a una base
que ya existe). El script lo clasifica como `columna nueva (aditivo)`, nivel REVISAR. La receta
completa está en [`MODELO_DATOS.md`](MODELO_DATOS.md).

**Qué no.** Cambiar o quitar una columna, un `CHECK`, un `UNIQUE` o el disparador. SQLite no
permite alterar un `CHECK` con `ALTER TABLE`: habría que reconstruir la tabla, y eso es rehacer la
medición.

## Gate, placebo y veredicto

**Qué cubre:** `app/trading/measurement_status.py`, `app/trading/ai_value.py`,
`app/trading/ai_value_placebo.py`, `app/trading/ai_value_report.py`, `app/trading/verdict_record.py`,
`scripts/ai_value_report.py` y `scripts/placebo_check.py`; y el archivo
`docs/VEREDICTO_3_6_REGISTRO*.txt`, que **no se crea a mano**.

**Por qué.** Estas son las reglas de decisión, fijadas antes de ver datos, y el procedimiento que
evita sesgarlas:

- **Punto de análisis único.** `is_measurement_gate_open` es la única fuente de verdad: abre solo
  cuando ambos lados tienen al menos `MIN_EFFECTIVE_N` conglomerados efectivos. Antes de eso nadie
  mira resultados: `measurement_status.py` ni siquiera lee `pnl_net_usdt`, `pnl_gross_usdt`,
  `exit_price` ni `close_reason`, y `ai_value_report.py` empieza por el estado de conteos y se
  detiene sin ejecutar la consulta que lee resultados si el gate está cerrado.
- **Los tres veredictos** y el orden de las condiciones (`ai_value.py`): primero
  `MAX_SIN_LLM_SHARE` (sin mirar el intervalo), luego el IC al 95 % contra cero y `DELTA_PCT`.
- **Placebo antes del veredicto.** Con el gate abierto: copia consistente de la base, placebo
  oficial (1000 repeticiones) y, solo si sale `CALIBRADO`, el informe **una sola vez** sobre esa
  misma copia. Si el placebo sale `NO INFORMATIVO` (menos del 50 % de las repeticiones llega al
  punto de análisis), no se corre el veredicto y se esperan 7 días más de datos. La espera depende
  solo de conteos, nunca de resultados.
- **El registro oficial.** `scripts/ai_value_report.py --out docs/VEREDICTO_3_6_REGISTRO_<fecha>.txt`
  lo crea; **solo puede haber uno** (`--out` se rechaza con código 5 si ya hay otro válido). Su
  existencia con una línea `Veredicto: <X>` válida, junto con el gate abierto, es lo único que
  permite mostrar resultados (`is_outcome_reveal_allowed`). Se lee en cada petición: aparece el
  archivo y los resultados se desbloquean sin reiniciar.
- **El registro del placebo** (`docs/PLACEBO_<fecha>.txt`) tiene **a propósito un nombre que no
  cumple el patrón** del registro: si lo cumpliera, desbloquearía los resultados sin veredicto.

**Qué no se hace nunca.**

- Cambiar una constante de `ai_value.py` o de `ai_value_placebo.py` después de ver datos.
- Crear o renombrar a mano un archivo `docs/VEREDICTO_3_6_REGISTRO*.txt` (el script de seguridad
  lo señala como `registro oficial del veredicto creado a mano`).
- Dar al registro del placebo un nombre que cumpla el patrón del registro del veredicto.
- Correr las herramientas contra la base viva: se usan siempre sobre una copia
  (`scripts/backup_db.py`, o `scripts/docker_db.py export` con el bot en Docker).
- Agregar a `measurement_status.py` cualquier columna de resultado.

**Cambios que sí ocurrieron** (y por qué no contaminan): `placebo_check.py` se reescribió el
2026-10-17, antes de abrirse el gate, y el criterio de calibración (tasa condicional) se decidió el
mismo día antes de ver datos. Ver [la lista de cambios](#cambios-ya-hechos-en-zonas-protegidas-desde-el-congelamiento).

## Ocultamiento de resultados

**Qué cubre:** `app/trading/reveal.py`, `app/memory/gate.py` y `app/api/serializers.py`.

**Por qué.** Mirar resultados antes del punto de análisis contaminaría el veredicto (regla del
2026-10-17). La condición para mostrar PnL, precio de salida o motivo de cierre es **una sola**:
`is_outcome_reveal_allowed` (gate abierto **y** registro oficial). Las dos puertas que la usan
(`reveal.reveal_allowed` y `memory.gate.results_visible`) **fallan cerradas**: ante cualquier error
al evaluarla, los resultados siguen ocultos.

**Capas** (todas deben mantenerse):

1. **Lista blanca, nunca negra** (`shadow_trade_to_public_dict`): `_ALWAYS_VISIBLE_FIELDS` y
   `_OUTCOME_FIELDS`. Un campo nuevo de `ShadowTrade` **no sale** hasta que alguien lo agregue a
   mano; jamás `trade.model_dump()` ni un atajo "todo menos lo prohibido". Comisiones, slippage,
   funding, `best_price` y `effective_stop` quedan fuera incluso con el reveal abierto.
2. **Nada de `response_model`** con `pnl_net_usdt`: FastAPI filtraría por el modelo, pero el valor
   real se serializaría igual. Las rutas devuelven `list[dict]`.
3. **La cuenta paper también está oculta**: `GET /trades` y `GET /positions`
   (`tests/unit/test_paper_account_results_hidden.py`) y los comandos de Telegram que muestran
   balance, equity, PnL flotante o factor de beneficio (`tests/unit/test_telegram_commands.py` y
   `tests/unit/test_telegram_metrics.py`). Telegram consulta `is_outcome_reveal_allowed` directamente;
   `reveal.reveal_allowed` es la versión que además falla cerrada ante un error.
4. **El dashboard falla cerrado**: `frontend/src/SummaryTab.tsx` solo muestra las columnas de
   resultado si `outcome_reveal_allowed === true`, aunque el backend las mandara.
5. **Los informes** (`scripts/shadow_report.py`, `scripts/real_money_report.py`,
   `scripts/lessons_report.py`) y las **notificaciones push** obedecen la misma puerta.
6. **`measurement_status.py` y `/api/measurement/status`** solo exponen conteos.

**Lo que NO cubre (límites conocidos).** Los **registros de log**: `logs/minerva.log` escribe
`Monitor cerro trade=… motivo=… precio=…` para cada cierre —sombras y cuenta paper— y
`Posicion cerrada (paper) … pnl_neto=…` para la cuenta paper. Quien lea el log ve motivos de cierre
y PnL de la cuenta aunque la puerta esté cerrada. No se tocó para no cambiar el comportamiento; si
vas a compartir logs, quítales esas líneas.

**Cómo se prueba:** `tests/unit/test_outcome_reveal.py`, `tests/unit/test_serializers.py`,
`tests/unit/test_paper_account_results_hidden.py` y el barrido de punta a punta
`tests/e2e/test_no_leaks.py`, que recorre todos los canales. Ver [`PRUEBAS.md`](PRUEBAS.md).

## Parámetros de configuración protegidos

**Qué cubre:** los archivos donde se escribe el valor de una variable protegida: `app/config.py`
(el valor por defecto), `.env.example` (la copia documentada de esos valores),
`docker-compose.yml` y `scripts/start_bot.ps1` (los interruptores que fijan el bot real).

**Por qué.** Una variable protegida cambia lo medido sin tocar código. Un valor puede venir de tres
lugares y gana el primero que exista: la variable de entorno del proceso, el `.env` del directorio
de trabajo y el valor por defecto de `app/config.py`. **El script de seguridad solo ve el
repositorio**: no ve tu `.env` ni el entorno del contenedor. Para saber qué usa el bot que está
corriendo, mira `GET /health` y corre `scripts/measurement_identity.py` sobre una copia.

| Variable | Defecto | Zona |
|---|---|---|
| `ANTHROPIC_SONNET_MODEL` | `claude-sonnet-5-5` | Etiquetador y medición |
| `LLM_MAX_TOKENS` | `300` | Etiquetador y medición |
| `LLM_TIMEOUT_SECONDS` | `20.0` | Etiquetador y medición |
| `LLM_MAX_CONCURRENCY` | `4` | Etiquetador y medición |
| `LLM_LIVE_LABELING_ENABLED` | `false` | Etiquetador y medición |
| `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` | `ema_cross_9_21,funding_contrarian_experimental,funding_contrarian_percentile_experimental` | Estrategias y parámetros |
| `UNIVERSE_SIZE` | `10` | Universo |
| `UNIVERSE_CANDIDATE_POOL` | `30` | Universo |
| `UNIVERSE_EXCLUDE_CATEGORIES` | `stablecoins,wrapped-tokens,liquid-staking-tokens` | Universo |
| `UNIVERSE_MANUAL_EXCLUSIONS` | (vacío) | Universo |
| `UNIVERSE_PRICE_SANITY_TOLERANCE_PCT` | `0.05` | Universo |
| `UNIVERSE_REFRESH_HOURS` | `24.0` | Universo |
| `UNIVERSE_STALENESS_HOURS` | `48.0` | Universo |
| `SCHEDULER_POLL_SECONDS` | `60` | Agrupación de señales y ciclo del scheduler |
| `CANDLE_STALE_TOLERANCE_SECONDS` | `300.0` | Agrupación de señales y ciclo del scheduler |
| `FUNDING_STALE_MARGIN_HOURS` | `1.0` | Agrupación de señales y ciclo del scheduler |
| `BACKTEST_FALLBACK_SL_PCT` | `0.05` | Niveles de SL y TP |
| `BACKTEST_FALLBACK_TP_PCT` | `0.10` | Niveles de SL y TP |
| `LEVERAGE` | `10` | Niveles de SL y TP |
| `LIVE_SL_MARGIN_CAP_PCT` | `50.0` | Tope de SL |
| `DEFAULT_MARGIN_USDT` | `5.0` | Sombras |
| `TAKER_FEE_PCT` | `0.0006` | Sombras |
| `BACKTEST_SLIPPAGE_BPS` | `5.0` | Sombras |
| `TP_GAP_TOLERANCE_PCT` | `0.002` | Sombras |

Los defectos de la tabla los comprueba una prueba contra `app/config.py`. Las variables de las
[zonas para revisar](#zonas-para-revisar) están en cada una de esas secciones.

**Qué detecta el script aquí.** Un cambio del valor por defecto en `app/config.py`; que
`.env.example` deje de coincidir con ese defecto; que una línea de `docker-compose.yml` o de
`scripts/start_bot.ps1` que fija la variable cambie o desaparezca (una línea nueva se avisa como
REVISAR). Los comentarios y el bloque de ayuda de `start_bot.ps1` no cuentan.

**Trampas.**

- **Escalas.** Los campos `*_pct` mezclan fracción (0-1) y porcentaje (0-100): `TAKER_FEE_PCT` y
  `TP_GAP_TOLERANCE_PCT` son fracciones; `LIVE_SL_MARGIN_CAP_PCT` es porcentaje. `app/config.py`
  valida las de riesgo en vivo al arrancar y falla fuerte.
- **`LEVERAGE` y `DEFAULT_MARGIN_USDT` se leen en varios lugares** (generador, sombras, cuenta paper
  y reportes); cambiarlos en uno solo no es posible, y cambiarlos en todos es cambiar la medición.
- **Quitar una variable de `.env.example`** no cambia el comportamiento, pero `tests/unit/test_env_example.py`
  exige que estén todas.

## Zonas para revisar

No invalidan la medición por sí solas, pero un cambio descuidado puede **mostrar resultados antes
de tiempo, detenerla, degradarla o cambiar el entorno donde corre**. El script las lista bajo
"PARA REVISAR" y no hacen fallar el comando salvo con `--strict`. Cada una pide un criterio
explícito antes de tocarla.

### Cuenta paper y brazo ejecutado

**Qué cubre:** `app/trading/risk_engine.py`, `app/trading/paper_execution.py` y
`app/execution/paper_backend.py`. Variables: `MAX_SIMULTANEOUS_POSITIONS`,
`MAX_SAME_DIRECTION_POSITIONS`, `MAX_DRAWDOWN_PCT`, `MAX_DAILY_LOSS_PCT`,
`MAX_CAPITAL_PCT_PER_ASSET`, `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`, `CIRCUIT_BREAKER_COOLDOWN_HOURS`,
`DRAWDOWN_STOP_MODE`, `INITIAL_CAPITAL_USDT`, `LLM_REAL_MAX_DELAY_SECONDS`,
`PAPER_EXECUTION_ENABLED`, `AUTO_OPEN_WITHOUT_LLM`, `MIN_PAPER_TRADING_DAYS`, `MIN_CLOSED_TRADES` y
`MIN_PROFIT_FACTOR`.

**Por qué es solo REVISAR.** Las sombras no pasan por el motor de riesgo (margen ilimitado, sin
cupos): estas piezas **no cambian lo medido**. Lo que cambian son las reglas con las que se
evaluará el **brazo ejecutado** (criterios de paso a dinero real, subfase 3.8): si cambian a
mitad, los 30 días y las 100 operaciones mezclan dos conjuntos de reglas.

**Trampas.**

- **`AUTO_OPEN_WITHOUT_LLM` debe seguir en `false`.** El brazo se niega a armarse (CRITICAL) si ambos
  interruptores están en true, porque mezclaría operaciones sin LLM en la cuenta. Es 100 % paper:
  nada de este código envía órdenes reales.
- `real_arm_started_at` y `real_arm_drawdown_baseline` se fijan una sola vez al armar el brazo;
  reiniciar los criterios es una acción manual deliberada.
- `LIVE_SL_MARGIN_CAP_PCT` y `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` también las lee el motor de riesgo,
  pero **no son de esta zona**: son protegidas (ver [Tope de SL](#tope-de-sl) y
  [Estrategias y parámetros](#estrategias-y-parámetros)).

### Parámetros operativos

**Variables:** `LLM_DAILY_BUDGET_USD`, `LLM_PRICE_INPUT_PER_MTOK`, `LLM_PRICE_OUTPUT_PER_MTOK`,
`MONITOR_TICK_SECONDS`, `TICK_STALE_SECONDS`, `WS_STALE_AFTER_SECONDS` y `REPORT_TIMEZONE`.

No cambian el significado de lo medido, pero un valor malo degrada la medición:

- Un `LLM_DAILY_BUDGET_USD` demasiado bajo agota el presupuesto y deja grupos `SIN_LLM`
  (`BUDGET_EXCEEDED`); si superan `MAX_SIN_LLM_SHARE` el veredicto será `INCONCLUSO`. Subirlo no
  cambia lo medido, pero es gasto real de la API de Claude (lo único que cuesta dinero en el
  proyecto): decídelo tú.
- Los precios por millón de tokens solo afectan a la contabilidad del costo y a la reserva del
  presupuesto: un precio mal puesto corta el presupuesto antes o después de lo debido.
- Los tiempos del monitor cambian cuánto tarda en reaccionar a un tick o a un feed obsoleto.
- `REPORT_TIMEZONE` fija la medianoche del "día" del presupuesto y de la pérdida diaria.

### Fuentes de datos de mercado

**Qué cubre:** `app/market/bitunix_rest.py`, `app/market/bitunix_ws.py` y
`app/market/contract_specs.py`.

Un arreglo aparentemente inocente aquí (paginación, unidades, tipo de precio, reintentos) puede
cambiar en silencio las velas, el funding o los ticks que ven las estrategias y el monitor. No
invalida por sí solo, pero exige comprobar que los datos que salen son los mismos que antes. No
inventes endpoints ni parámetros de Bitunix: verifícalos con su documentación oficial.

### Canales que muestran datos de operaciones

**Qué cubre:** `app/api/routes/*.py`, `app/telegram/commands.py`, `app/telegram/push.py`,
`app/trading/shadow_report.py`, `app/trading/real_money_report.py`, `scripts/shadow_report.py`,
`scripts/lessons_report.py`, `scripts/real_money_report.py`, `scripts/telegram_daily.py` y
`frontend/src/SummaryTab.tsx`.

Cada uno de estos archivos lee la base y presenta datos: **una línea de más puede mostrar
resultados antes de tiempo**. Antes de agregar un campo o un comando:

- Todo lo que salga de `shadow_trades` pasa por `shadow_trade_to_public_dict`.
- Todo lo que muestre PnL, balance, equity o factor de beneficio de la cuenta paper consulta
  primero la puerta: `is_outcome_reveal_allowed`, o `reveal.reveal_allowed` si quieres que además
  falle cerrada ante un error.
- No declares `response_model` con un modelo que tenga `pnl_net_usdt`.
- Las 22 rutas de la API están contadas por una prueba: añadir una ruta exige actualizarla.

### Controles que detienen la medición

**Qué cubre:** `app/trading/control_state.py`, `app/trading/control_actions.py` y
`app/api/routes/control.py`.

**Pausar** y el **kill switch** (que además pausa el scheduler) **detienen la medición**: sin
señales, sin sombras y sin etiquetado mientras dure; los cierres de posiciones siguen. La
advertencia (`STOPS_MEASUREMENT`) es una sola constante que muestran Telegram y el dashboard, y la
confirmación por palabra exacta (`PAUSAR`, `REANUDAR`, `KILL`) no puede relajarse. La pausa se guarda
en `system_state` y sobrevive a un reinicio.

### Arranque del proceso

**Qué cubre:** `app/main.py`.

El `lifespan` crea el etiquetador, el scheduler, el monitor y el resto de las tareas. Un error de
cableado (olvidar pasar el etiquetador al scheduler, cambiar el orden de arranque) puede dejar la
medición parada **sin que nada falle**: el bot arranca y `GET /health` sigue respondiendo. Tras
cualquier cambio aquí, comprueba `llm_labeling.enabled` y `llm_labeling.frozen_hash_ok` en
`GET /health` y que aparezcan filas nuevas de `MEDICION`. El orden está en
[`FLUJO_EJECUCION.md`](FLUJO_EJECUCION.md).

### Entorno de ejecución

**Qué cubre:** `pyproject.toml`, `docker/requirements.lock` y `Dockerfile`.

Un cambio de versión de pandas, numpy o pydantic puede alterar indicadores o validaciones sin que
ninguna prueba lo vea, y la imagen del bot real fija las versiones exactas. Cambiar una dependencia
es REVISAR: corre toda la batería, compara los indicadores y despliega solo en la ventana segura
(ver [`RECETAS.md`](RECETAS.md)).

### Prompt v5 preparado

**Qué cubre:** `app/llm/prompts_v5.py`.

Es el prompt de la **siguiente** medición (con contexto de noticias y lecciones), preparado y sin
uso. Editarlo no cambia la medición actual, pero **importarlo desde cualquier parte del bot la
invalidaría**: `tests/unit/test_prompt_v5_prepared.py` y las pruebas de aislamiento de noticias y
memoria exigen que nada lo importe. Usarlo es una medición nueva: ver
[Si de verdad hay que cambiar algo protegido](#si-de-verdad-hay-que-cambiar-algo-protegido).

### Pruebas guardianas

**Qué cubre:** `tests/e2e/test_no_leaks.py`, `tests/e2e/leaks.py`,
`tests/unit/test_outcome_reveal.py`, `tests/unit/test_paper_account_results_hidden.py`,
`tests/unit/test_serializers.py`, `tests/unit/test_llm_prompts.py`,
`tests/unit/test_llm_market_features.py` y `tests/unit/test_shadow_live_labeler.py`.

**Debilitar una de estas pruebas quita una protección.** Un cambio aquí exige explicar por qué la
protección ya no hace falta, no solo que "la prueba molestaba". La lista completa de guardianes y
lo que protege cada uno está en [`PRUEBAS.md`](PRUEBAS.md).

## Qué se puede cambiar con seguridad

Con los candados de arriba como red de seguridad, esto se puede tocar sin invalidar la medición.
"Seguro" significa que el cambio no entra en una zona; **sigue exigiendo pruebas, ruff y, si va al
contenedor, el despliegue en la ventana segura** (ver [`RECETAS.md`](RECETAS.md)).

| Qué | Condición |
|---|---|
| **Documentación** (`docs/`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`) | nunca crees ni renombres `docs/VEREDICTO_3_6_REGISTRO*.txt`; `docs/` va montada en el contenedor y se lee en cada petición |
| **Docstrings y comentarios** de cualquier archivo | salvo `app/llm/prompts.py` y `app/llm/market_features.py`, que son byte a byte; el script los clasifica como `solo documentacion` |
| **Aspecto del dashboard** (`frontend/src/`) | sin tocar la regla de `SummaryTab.tsx` ni mostrar campos que la API no manda |
| **Textos y formato de Telegram** | un comando nuevo o un campo nuevo que lea operaciones debe pasar por la puerta de resultados |
| **Scripts de operación** (copias de seguridad, restauración, salud, exportación) | siempre sobre una copia de la base; los que leen la base real se ensayan antes contra una copia temporal |
| **Colector de noticias** (`app/news/`) | está aislado y no alimenta el prompt congelado; `tests/unit/test_news_runtime_isolation.py` lo vigila |
| **Memoria de lecciones** (`app/memory/`, salvo `gate.py`) | `LESSONS_MEMORY_ENABLED` es `false` en todo momento durante la medición |
| **Registro de eventos, latido, cliente HTTP** (`app/core/logging.py`, `app/core/heartbeat.py`, `app/core/http.py`) | sin añadir resultados a los logs |
| **Motor de backtest** (`app/backtesting/engine.py`, `report.py`, `risk_analysis.py`) | el camino vivo solo usa de `app/backtesting/` tres módulos: `liquidation.py` y `funding.py` (protegidos) y `metrics.py` (lo importa el reporte de paso a dinero real: cambiarlo cambia ese reporte) |
| **Pruebas nuevas** | las guardianas no se debilitan |
| **Una columna nueva** en una tabla medida | aditiva, nula o con valor por defecto, que el camino medido no escriba (ver [Esquema](#esquema-de-las-tablas-de-la-medición)) |

## Cambios ya hechos en zonas protegidas desde el congelamiento

La medición arrancó con el congelamiento del 2026-10-12 (commit `c13f8ca`). Desde entonces
`python scripts/check_measurement_safety.py --since-measurement` lista cambios en zonas
protegidas. Se revisaron uno por uno; esta es la foto del 2026-10-24 con el motivo de cada uno, para
que no haya que reabrir la investigación cada vez que el script los vuelva a mostrar.

**Código que cambió en archivos que ya existían:**

| Archivo | Qué cambió | Commit | ¿Altera lo medido? |
|---|---|---|---|
| `app/core/scheduler.py` | pausa persistente: con el scheduler pausado `run_cycle` no genera nada; además pasa el candidato a `maybe_label` | `2b3c5af`, `5d30ae2` | no, mientras no se pause. Pausar detiene la medición **a propósito**, con advertencia |
| `app/trading/shadow_live_labeler.py` | gancho opcional `on_decision`, llamado **después** de persistir la etiqueta | `5d30ae2` | no: una prueba comprueba que la medición es idéntica con el brazo encendido y apagado (`test_the_measurement_is_identical_with_the_arm_on_and_off`) |
| `app/persistence/repositories/shadow_repo.py` | `_row_to_shadow` pasó a pública (`row_to_shadow`) y se añadió `get_recent` | `f54eb63`, `a09c371` | no: solo renombrado y una lectura nueva |
| `app/persistence/repositories/llm_logs_repo.py` | se añadió `get_recent` (lectura) | `a09c371` | no |
| `app/persistence/repositories/universe_repo.py` | se añadió `get_included_history` (lectura) | `aab88f9` | no |
| `app/strategies/registry.py` | se quitó `get_strategy` (sin ninguna referencia) | `37820f2` | no |
| `scripts/placebo_check.py` | reescrito: lee la copia en solo lectura, usa los filtros del veredicto, acepta `--out` y decide con la tasa condicional | `14dab62`, `3495d79`, `6d7c76f` | no sobre los datos; define el procedimiento del placebo, decidido el 2026-10-17 **antes** de abrirse el gate |

**Archivos protegidos que no existían al congelarse** (nuevos, de lectura, análisis u ocultamiento):
`app/trading/measurement_status.py`, `app/trading/ai_value_report.py`,
`app/trading/ai_value_placebo.py`, `app/trading/verdict_record.py`, `app/trading/reveal.py`,
`app/memory/gate.py`, `app/api/serializers.py`, `scripts/ai_value_report.py` y
`tests/unit/test_prompt_v5_prepared.py`.

**Variables:** `LLM_LIVE_LABELING_ENABLED`, que `scripts/start_bot.ps1` y `docker-compose.yml`
fijan para el bot real, aparece como "variable nueva en el archivo" porque esas líneas se
introdujeron después (la mudanza a Docker). El código y el prompt de la medición no cambiaron.

Todo lo demás que el script muestra como protegido en esa comparación es `solo documentacion`
(los docstrings completos de la tarea de documentación técnica). **No hay ningún cambio en el
prompt, las features, las estrategias, los niveles, las sombras ni el hash congelado.**

## Si de verdad hay que cambiar algo protegido

Antes de empezar, pregúntate si puede esperar. **Casi siempre puede.** Estas son las opciones, de
la más segura a la más cara:

**1. Esperar al veredicto (recomendado).** Cuando exista el registro oficial
(`docs/VEREDICTO_3_6_REGISTRO*.txt`), la medición terminó y los resultados se pueden ver. Desde ahí
puedes cambiar lo que quieras: lo que cambie solo afecta a una medición *nueva*, si quieres hacerla.

**2. Un cambio que de verdad no altera lo medido** (corregir un bug que no cambia señales,
etiquetas ni resultados, o añadir algo aditivo). Para tocar una zona protegida así:

- Escribe primero una **prueba de invarianza**: el mismo escenario con y sin el cambio produce
  `llm_logs`, `shadow_trades`, el estado de la medición y `measurement_keys` idénticos. El modelo
  a seguir es `tests/unit/test_paper_execution_isolation.py`.
- No toques `app/llm/prompts.py` ni `app/llm/market_features.py`: ni un byte.
- Anota el cambio y el motivo en `docs/historial/PROGRESS.md` y, si queda en una zona protegida,
  añádelo a la tabla de arriba.
- Despliega solo en la ventana segura (abajo).

**3. Cerrar la medición a propósito.** Si el cambio es necesario y la medición no se puede
terminar: decídelo, anótalo en `docs/historial/PROGRESS.md` y deja de etiquetar
(`LLM_LIVE_LABELING_ENABLED=false` y un reinicio). Lo medido hasta ahí **no tiene veredicto**: no
se usa para decidir nada.

**4. Empezar una medición nueva con otro hash.** Es lo que se hace para cambiar el prompt, las
features, el modelo o las estrategias. Pasos, en este orden:

1. Decidir qué cambia y por qué, y escribirlo antes de ver ningún dato nuevo.
2. Cambiar el contenido y **subir `PROMPT_VERSION` y/o `FEATURES_VERSION`**; `PROMPT_SHA256` cambia
   solo.
3. Actualizar a mano `FROZEN_PROMPT_SHA256` en `app/trading/shadow_live_labeler.py`.
4. Actualizar a mano las huellas de `tests/unit/test_prompt_v5_prepared.py` (`FROZEN_COMBINED_SHA256`,
   `V4_SYSTEM_PROMPT_SHA256`, `V4_PROMPTS_PY_SHA256`, `V4_MARKET_FEATURES_PY_SHA256`).
5. Si cambia el modelo, el nuevo valor de `ANTHROPIC_SONNET_MODEL` queda registrado en las filas;
   `scripts/measurement_identity.py` debe mostrar una única identidad.
6. Actualizar `MEASUREMENT_START` en `scripts/check_measurement_safety.py` y la lista de zonas si
   cambian los archivos protegidos, y este documento.
7. Desplegar en la ventana segura. Las filas viejas quedan en `llm_logs` con su hash (historial)
   pero **fuera de `measurement_keys`**, porque `measurement_keys` toma el hash de la fila más
   reciente. La medición nueva necesita sus propios 300 conglomerados por lado, su placebo y su
   veredicto.

**Ventana segura para reiniciar** (ver [`RECETAS.md`](RECETAS.md) y
[`../USO.md`](../USO.md) para el procedimiento completo): entre los minutos 10 y 40 de una hora
UTC que **no** sea de cierre de vela de 4 horas (00, 04, 08, 12, 16 y 20). Ahí no hay etiquetado en
vuelo que el cierre cancele, y la siguiente tanda de señales llega al menos 20 minutos después.
El contenedor sigue con el código viejo hasta que se reconstruye la imagen y se recrea.

**Lo que nunca se hace**, ni con aprobación a medias: detener, matar o reiniciar el bot real a
mitad de una vela; tocar `data/` o sus respaldos; mezclar dos hashes bajo el mismo
`measurement_keys` esperando que "no se note"; editar `FROZEN_PROMPT_SHA256` para que el etiquetador
vuelva a escribir con un prompt modificado.

## Lo que este documento no puede garantizar

- **La lista de zonas es manual.** Una prueba comprueba que cada archivo y cada variable de la
  lista existan y estén explicados, no que la lista sea completa. Si añades un módulo que alimenta
  una zona (por ejemplo, otra fuente de datos para las estrategias), añádelo a `ZONES` y a este
  documento.
- **Los límites del script** (el `.env` real, el contenedor, la semántica) están en
  [Cómo comprobarlo](#cómo-comprobarlo-scriptscheck_measurement_safetypy).
- **La estimación de efectos** ("apenas cambia", "no altera") es razonamiento, no medición: donde
  no se probó con una prueba de invarianza, se tomó la opción conservadora y se protegió.
- **Lo no verificado:** cuánto cambiaría el veredicto si `DEFAULT_MARGIN_USDT` o `LEVERAGE` cambiaran
  a mitad de la medición (el diseño dice que el retorno sobre el margen es comparable, pero no hay
  una medición de ello); y el efecto de actualizar dependencias sobre los indicadores (no hay una
  prueba que compare resultados numéricos entre versiones).
