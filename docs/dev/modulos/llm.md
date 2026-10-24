# Guía del paquete `llm`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/llm.html`](../api/app/llm.html)

## Qué hace el paquete

Todo lo que rodea a la decisión de Claude sobre una señal agrupada: el **mensaje** que ve el modelo (prompt congelado y
*features*), el **cliente** que llama a la API, el **servicio** que valida la respuesta, la registra en `llm_logs` y
etiqueta la sombra, y el **presupuesto diario** con reserva bajo candado. El LLM solo **revisa** señales (`APROBAR` /
`RECHAZAR`); nunca abre ni cierra nada.

## Zona protegida

**Sí, casi todo el paquete.** El prompt y las *features* están **congelados** (hash en el código y en las pruebas); el
modelo, los parámetros de la llamada y la validación de la respuesta tampoco se pueden cambiar sin invalidar la medición.
Dos archivos están además **fijados byte a byte** por una prueba y **no se pueden editar ni para un comentario**:
`app/llm/prompts.py` y `app/llm/market_features.py`. Ver [Prompt y features](../ZONAS_PROTEGIDAS.md#prompt-y-features) y
[Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición).

## Cómo se conecta

- `ShadowLiveLabeler` (`app/trading/shadow_live_labeler.py`) construye el mensaje con `build_features_from_shadow_trade` y
  `build_user_message`, y llama a `LlmDecisionService.decide_group` (`fase='MEDICION'`).
- `LlmClient` es **el único punto de contacto con la red**. El cliente real (`_anthropic_client.py`) se importa de forma
  diferida desde `build_anthropic_client`, así que ninguna prueba sin red necesita el SDK.
- Las tablas: lee `signals`, `ohlcv_cache`, `funding_cache`, `contract_specs_cache`, `trades` y `llm_logs`; escribe `llm_logs`
  (siempre una fila por grupo) y `shadow_trades.llm_decision` (una sola vez, inmutable).
- El único ciclo de imports entre módulos del proyecto está aquí: `client.py` ↔ `_anthropic_client.py`, resuelto con el
  import diferido.

## Archivos

### `app/llm/__init__.py`

**Responsabilidad.** Docstring del paquete: dice que `prompts_v5.py` está preparado y **sin usar**.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/llm/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm.html`](../api/app/llm.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/llm/__init__.py -->

### `app/llm/_anthropic_client.py`

**Responsabilidad.** El cliente real del LLM con el SDK oficial de Anthropic. Solo se importa desde
`client.build_anthropic_client`; ninguna prueba sin red lo carga. Requiere `ANTHROPIC_API_KEY` en `.env`.

**Funciones y clases clave.**
- `AnthropicLlmClient.complete()` — llama a la API de mensajes con `thinking = {"type": "between_tools"}` y
  `output_config = {"effort": "low"}`; **sin** `temperature`, `top_p` ni `top_k`.

**Invariantes — no romper.**
- **`max_retries=0`:** el SDK reintenta por defecto timeouts, 408/409/429 y ≥ 500; con `timeout=20s` una sola llamada «lógica»
  podría estirarse hasta ~60 s, justo el límite de retraso para abrir en la cuenta paper (`LLM_REAL_MAX_DELAY_SECONDS`).
  «Una sola llamada por grupo, sin reintentos» también vale a nivel de transporte: un fallo deja `SIN_LLM` rápido.
- Los parámetros se verificaron el 2026-10-09 contra la documentación vigente: Sonnet 5.5 devuelve `400` si se envía
  `temperature`/`top_p`/`top_k` distinto del de la API, y también con `thinking.type = "disabled"`. Sin herramientas
  declaradas no genera razonamiento extendido y los 300 tokens quedan enteros para el JSON.

**Trampas conocidas.** Estos parámetros **no** están cubiertos por el hash del prompt: cambiarlos cambia la medición sin
que nada lo avise.

**Protección.** Sí: [Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición).

<!-- hechos:app/llm/_anthropic_client.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** sin página (módulo privado)
- **Importa (internos):** `app.config`, `app.llm.client`
- **Lo importan:** `app.llm.client`
- **Configuración que lee:** `ANTHROPIC_API_KEY`, `LLM_TIMEOUT_SECONDS`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:app/llm/_anthropic_client.py -->

### `app/llm/budget.py`

**Responsabilidad.** El presupuesto diario del LLM con **reserva bajo candado**.

**Funciones y clases clave.**
- `LlmBudgetTracker.try_reserve()` — reserva el costo **máximo** de la llamada si
  `gasto de hoy (llm_logs) + ya reservado + este costo` no supera `LLM_DAILY_BUDGET_USD`; `release()` lo libera.
- `local_day_start_utc()` — el inicio del día local (`REPORT_TIMEZONE`) en UTC.

**Invariantes — no romper.**
- Las decisiones corren en paralelo (semáforo `LLM_MAX_CONCURRENCY`): comprobar y reservar **tienen que** pasar por una
  sección crítica; sin ella varias llamadas verían «presupuesto libre» a la vez y juntas superarían el tope.
- `try_reserve` y `release` van siempre en par (`try/finally` en el llamador).

**Trampas conocidas.** Un presupuesto demasiado corto produce `BUDGET_EXCEEDED` → `SIN_LLM`; si pasan del 10 % de los grupos
de la medición, el veredicto sale `INCONCLUSO` automáticamente.

**Protección.** Sí: [Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición) (el presupuesto cambia qué grupos
llegan a tener etiqueta).

<!-- hechos:app/llm/budget.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/budget.html`](../api/app/llm/budget.html)
- **Importa (internos):** `app.config`, `app.persistence.database`, `app.persistence.repositories.llm_logs_repo`
- **Lo importan:** `app.api.routes.health`, `app.api.routes.risk`, `app.llm.decision_service`, `app.telegram.commands`, `app.trading.paper_execution`
- **Configuración que lee:** `LLM_DAILY_BUDGET_USD`, `REPORT_TIMEZONE`
- **Tablas que lee:** `llm_logs`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_llm_budget.py`
<!-- /hechos:app/llm/budget.py -->

### `app/llm/client.py`

**Responsabilidad.** El cliente del LLM **como interfaz**.

**Funciones y clases clave.**
- `LlmClient` (`Protocol`) — `complete(system, user, model, max_tokens)`; `LlmRawResponse` (texto, tokens y latencia),
  `LlmTimeoutError` y `LlmHttpError`.
- `FakeLlmClient` — cliente programable para pruebas, sin red.
- `build_anthropic_client()` — la fábrica del cliente real, con **import diferido a propósito**.

**Invariantes — no romper.**
- Ningún módulo debe importar `_anthropic_client` salvo esta fábrica.
- Un test comprueba que no se construye ningún cliente real: la fábrica se parchea para lanzar un error si se invoca.

**Trampas conocidas.** `LlmClient.complete` es un `Protocol` con cuerpo `...` en la misma línea y no admite docstring sin
cambiar el AST: se documenta en la clase.

**Protección.** Sí (es la frontera con la red de pago).

<!-- hechos:app/llm/client.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/client.html`](../api/app/llm/client.html)
- **Importa (internos):** `app.llm._anthropic_client`
- **Lo importan:** `app.llm._anthropic_client`, `app.llm.decision_service`, `scripts.llm_pilot`, `scripts.llm_smoke`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_full_cycle.py`, `tests/e2e/world.py`, `tests/unit/test_llm_anthropic_client.py`, `tests/unit/test_llm_decision_service.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_prompt_v5_prepared.py`, `tests/unit/test_shadow_live_labeler.py`
<!-- /hechos:app/llm/client.py -->

### `app/llm/decision_service.py`

**Responsabilidad.** Junta el cliente, `llm_logs`, el presupuesto y el semáforo de concurrencia. Por cada grupo hace **una**
llamada (sin reintentos), valida la respuesta, guarda **siempre** una fila en `llm_logs` y, si la decisión es válida, escribe
la etiqueta en `shadow_trades` (una sola vez, inmutable).

**Funciones y clases clave.**
- `LlmDecisionService.decide_group()` — calcula el retraso de decisión (`ahora − cierre de la vela`), reserva el presupuesto
  (si no cabe: fila `BUDGET_EXCEEDED` y `SIN_LLM`), llama, y según el resultado deja `OK` (etiqueta `APROBADA`/`RECHAZADA`),
  `TIMEOUT`, `ERROR_HTTP` o `INVALID` (los tres últimos, `SIN_LLM`). Recalcula `prompt_sha256` con la misma fórmula que
  `prompts.PROMPT_SHA256`.
- `real_open_allowed()` — la regla del brazo: solo `APROBADA` con `decision_delay_s <= LLM_REAL_MAX_DELAY_SECONDS`.
- `estimate_cost_usd()`, `LlmDecisionResult` y las constantes de estado (`STATUS_*`, `SIN_LLM`, `PILOTO`, `MEDICION`).

**Invariantes — no romper.**
- **Todo fallo deja `SIN_LLM`, jamás `RECHAZADA`.** Un fallo técnico no es un juicio negativo.
- `signal_group_key` es **única** en `llm_logs`: una fila duplicada hace fallar el `INSERT`.
- En los fallos `TIMEOUT` / `ERROR_HTTP` se registra el costo **estimado** de la entrada (no 0): un fallo del cliente no
  garantiza que la llamada no se facturara.
- Este servicio **nunca** abre ni cierra posiciones.

**Trampas conocidas.** `atr_pct` no lo pasa el etiquetador en vivo (queda `NULL` en `llm_logs`). El `prompt_sha256` cubre el
texto **y** `features_version`: antes cubría solo el texto y dos mensajes distintos podían compartir hash.

**Protección.** Sí: [Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición).

<!-- hechos:app/llm/decision_service.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/decision_service.html`](../api/app/llm/decision_service.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.llm.budget`, `app.llm.client`, `app.llm.schemas`, `app.persistence.database`, `app.persistence.repositories.llm_logs_repo`, `app.persistence.repositories.shadow_repo`
- **Lo importan:** `app.api.routes.health`, `app.trading.paper_execution`, `app.trading.shadow_live_labeler`, `scripts.llm_pilot`, `scripts.llm_smoke`
- **Configuración que lee:** `ANTHROPIC_SONNET_MODEL`, `LLM_MAX_CONCURRENCY`, `LLM_MAX_TOKENS`, `LLM_PRICE_INPUT_PER_MTOK`, `LLM_PRICE_OUTPUT_PER_MTOK`, `LLM_REAL_MAX_DELAY_SECONDS`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** `llm_logs`, `shadow_trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_health_route.py`, `tests/unit/test_llm_decision_service.py`, `tests/unit/test_paper_execution.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_prompt_v5_prepared.py`, `tests/unit/test_scheduler_pause.py`, `tests/unit/test_shadow_live_labeler.py`, `tests/unit/test_telegram_commands.py`, `tests/unit/test_telegram_e2e.py`
<!-- /hechos:app/llm/decision_service.py -->

### `app/llm/market_features.py`

**Responsabilidad.** Las *features* de mercado del mensaje: ATR, volatilidad, correlación con BTC, contexto de tendencia
(retornos de 24 h/72 h y distancia a la EMA50, todo en 1h), funding y posiciones reales abiertas. Todo sale de la caché local;
**nunca toca la red**.

**Funciones y clases clave.**
- `build_market_features()` — junta todo para una sombra; `fetch_closed_candles()` lee las velas cerradas.
- `compute_atr14_pct()`, `compute_returns_std_pct()`, `compute_btc_correlation()`, `compute_return_pct()`,
  `compute_ema_distance_pct()`, `compute_trend_features()`, `compute_funding_features()` y `compute_open_real_positions()`.

**Invariantes — no romper.**
- **Prueba de fuga:** cada función que lee velas recibe `as_of_ms` (el cierre de la vela evaluada) y solo usa velas
  **cerradas** antes o en ese instante. `tests/unit/test_llm_market_features.py` lo prueba con velas futuras de valores
  extremos.
- Timeframe de referencia fijo **1h** (`REFERENCE_TIMEFRAME`): es el más fino que ya se cachea para todo el universo.
- Si falta un dato, devuelve `None` explícito; **nunca se inventa**.

**Trampas conocidas.** El ATR(14) de este módulo es simple (promedio de 14 rangos verdaderos), no el suavizado de Wilder de
`app/indicators/engine.py`.

**Protección.** Sí, **fijado byte a byte**: ni un comentario. [Prompt y features](../ZONAS_PROTEGIDAS.md#prompt-y-features).

<!-- hechos:app/llm/market_features.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/market_features.html`](../api/app/llm/market_features.html)
- **Importa (internos):** `app.market.ohlcv_history`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.funding_repo`, `app.persistence.repositories.ohlcv_repo`, `app.persistence.repositories.specs_repo`, `app.persistence.repositories.trades_repo`
- **Lo importan:** `app.llm.prompts`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `contract_specs_cache`, `funding_cache`, `ohlcv_cache`, `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_check_measurement_safety.py`, `tests/unit/test_docstrings.py`, `tests/unit/test_llm_market_features.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/llm/market_features.py -->

### `app/llm/prompts.py`

**Responsabilidad.** El prompt de sistema **congelado** (`signal_review_v4`), la versión de *features* (`features_v2`), el hash
combinado `PROMPT_SHA256` y la construcción del mensaje de usuario.

**Funciones y clases clave.**
- `SYSTEM_PROMPT`, `PROMPT_VERSION = 'signal_review_v4'`, `FEATURES_VERSION = 'features_v2'` y
  `PROMPT_SHA256 = sha256(SYSTEM_PROMPT + "\n---FEATURES_VERSION---\n" + FEATURES_VERSION)`.
- `build_features_from_shadow_trade()` — junta lo que pide la sección (a) usando solo datos con cierre o instante ≤ el cierre de
  la vela evaluada: identidad, indicadores y timeframe de cada estrategia contribuyente (de `signals.indicators_json`),
  niveles y riesgo planeados **de la propia sombra** (nunca `effective_stop`/`best_price`, que seguirían el precio posterior
  a la decisión: eso sí sería fuga), distancias derivadas, `funding_effect_on_position` (calculado, no inferido) y las
  *features* de `market_features`.
- `build_user_message()` — JSON con claves ordenadas y nulos explícitos; `estimate_input_tokens()` — caracteres / 2.

**Invariantes — no romper.**
- El mensaje lleva solo datos de la señal y del mercado **ya disponibles** al cierre de la vela: no recibe velas posteriores,
  resultados, historial de aciertos ni noticias. Un dato que falta queda `null` explícito.
- Un defecto del modelo que resistió la explicación en texto (el signo del funding en posiciones cortas) se resolvió
  **calculándolo** en código y entregándole la respuesta hecha.

**Trampas conocidas.** `estimate_input_tokens` usa caracteres / 2 a propósito: con caracteres / 3 se subestimaba un 25 %. El
texto del prompt tiene algún carácter acentuado; **no se normaliza ni se reformatea**.

**Protección.** Sí, **fijado byte a byte**: ni un comentario. [Prompt y features](../ZONAS_PROTEGIDAS.md#prompt-y-features).

<!-- hechos:app/llm/prompts.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/prompts.html`](../api/app/llm/prompts.html)
- **Importa (internos):** `app.config`, `app.llm.market_features`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.signals_repo`
- **Lo importan:** `app.llm.prompts_v5`, `app.trading.shadow_live_labeler`, `scripts.llm_pilot`, `scripts.llm_smoke`
- **Configuración que lee:** `FUNDING_STALE_MARGIN_HOURS`
- **Tablas que lee:** `signals`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_atr_from_prompt.py`, `tests/unit/test_check_measurement_safety.py`, `tests/unit/test_docstrings.py`, `tests/unit/test_llm_prompts.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_prompt_v5_prepared.py`, `tests/unit/test_public_docs.py`, `tests/unit/test_shadow_live_labeler.py`
<!-- /hechos:app/llm/prompts.py -->

### `app/llm/prompts_v5.py`

**Responsabilidad.** El prompt v5 / *features* v3: **PREPARADOS, SIN USAR** (Fase 4). Agregan un párrafo que declara los
titulares como texto no confiable de terceros y las lecciones como estadística del pasado, y tres claves siempre presentes
(`news_context`, `news_status`, `lessons_context`).

**Funciones y clases clave.**
- `SYSTEM_PROMPT_V5` (el de v4 byte a byte más un párrafo), `PROMPT_SHA256_V5`, `add_external_context()`,
  `build_features_v3()` y `build_user_message_v5()`.

**Invariantes — no romper.**
- **Ningún módulo de la aplicación ni de los scripts lo importa**; solo las pruebas (una lo verifica por AST y otra comprueba
  que `import app.main` no lo carga). El contexto de lecciones exige `is_outcome_reveal_allowed`.
- Se activa **después** del veredicto, con aprobación del autor, como una **medición nueva** con otro hash.

**Trampas conocidas.** Peor caso medido: +6.600 caracteres por decisión (unos +2.900 a +3.300 tokens de entrada frente a los
~1.400 de v4); el presupuesto diario se recalibra al activar.

**Protección.** Se puede editar sin invalidar la medición, siempre que siga sin importarse desde la aplicación.

<!-- hechos:app/llm/prompts_v5.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/prompts_v5.html`](../api/app/llm/prompts_v5.html)
- **Importa (internos):** `app.config`, `app.llm.prompts`, `app.memory.features`, `app.memory.gate`, `app.memory.retrieval`, `app.news.context`, `app.persistence.database`, `app.persistence.models`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/llm/prompts_v5.py -->

### `app/llm/schemas.py`

**Responsabilidad.** El esquema de salida del LLM, validado con pydantic: `LlmDecisionOut` (`decision`, `confianza` en [0, 1],
`razonamiento` ≤ 300) y `parse_decision()`.

**Funciones y clases clave.**
- `parse_decision()` — valida la respuesta cruda; **trunca** un `razonamiento` demasiado largo en vez de invalidar y repara la
  única forma de JSON roto conocida (la comilla de cierre de `razonamiento` omitida, ver
  `_try_repair_unterminated_razonamiento`), devolviendo `repaired=True` para auditarlo (`llm_logs.json_repaired`).
- `DECISION_TO_LABEL` — `APROBAR → APROBADA`, `RECHAZAR → RECHAZADA`.

**Invariantes — no romper.**
- `extra="forbid"` y los límites hacen que cualquier campo extra, fuera de rango o texto antes/después del JSON sea una
  respuesta **inválida** (`SIN_LLM`, nunca `RECHAZADA`).
- El reparo es **muy acotado** a propósito: cualquier otro JSON roto sigue sin repararse.

**Trampas conocidas.**
- *Piloto v1:* la única respuesta inválida de 21 fue un `razonamiento` de más de 300 caracteres con el resto perfecto; perder
  la decisión por eso no corresponde a un fallo real, de ahí el truncado.
- *Piloto v2:* 2 de 31 respuestas (6,5 %) omitieron la comilla de cierre; eran `APROBAR` y perderlas habría sesgado la
  medición, de ahí el reparo.
- Las salidas estructuradas (`output_config.format = json_schema`) **no se adoptaron**: ninguna página oficial confirma ni
  descarta la combinación exacta con `thinking: between_tools` y `effort: low`. Pendiente; no verificado.

**Protección.** Sí: [Etiquetador y medición](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición) (qué respuesta cuenta como válida).

<!-- hechos:app/llm/schemas.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/llm/schemas.html`](../api/app/llm/schemas.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.api.routes.llm_decisions`, `app.llm.decision_service`, `app.trading.measurement_status`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_llm_schemas.py`
<!-- /hechos:app/llm/schemas.py -->
