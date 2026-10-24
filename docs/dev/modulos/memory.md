# Guía del paquete `memory`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/memory.html`](../api/app/memory.html)

## Qué hace el paquete

La **memoria de lecciones** (Fase 4): aprender **sin reentrenar** de las operaciones ya **cerradas**. Guarda una entrada
inmutable por cada sombra cerrada y fiable, agrega patrones (lecciones) con su tasa de acierto, escribe un post-mortem de las
rachas perdedoras y calibra la confianza del LLM. **Está apagada** (`LESSONS_MEMORY_ENABLED=false`) y no se enciende en ningún
puerto durante la medición.

Lee **resultados (PnL)** para aprender, por eso todo lo que produce pasa por la puerta de resultados: ni la API, ni Telegram,
ni el dashboard, ni los scripts muestran una tasa de acierto o un retorno sin `is_outcome_reveal_allowed`.

## Zona protegida

**No,** con dos condiciones: (1) nada de este paquete entra al prompt congelado; (2) su salida con resultados sigue detrás de la
puerta. Si se enciende durante la medición, **lee resultados**: sería una fuga de información al operador (aunque no los
muestre) y no se debe hacer. Ver [Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados).

## Cómo se conecta

- `app/main.py` llama a `maybe_start_lessons_runner`, que crea la tarea solo con el interruptor encendido.
- Escribe `memory_entries`, `lessons`, `lessons_reports` y `lesson_applications`; lee `shadow_trades`, `llm_logs`, `news_items`,
  `fear_greed` y `trades`.
- Lo usan `scripts/lessons_report.py` (informe con puerta) y el prompt v5 preparado (`app/llm/prompts_v5.py`, sin usar).

## Archivos

### `app/memory/__init__.py`

**Responsabilidad.** Docstring del paquete: aprender sin reentrenar, fuera del prompt congelado y detrás de la puerta.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/memory/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory.html`](../api/app/memory.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/memory/__init__.py -->

### `app/memory/calibration.py`

**Responsabilidad.** La calibración de la confianza del LLM: por tramos de `confianza` (0-1), cuántas de las operaciones que el LLM
aprobó (o rechazó) terminaron ganando. **Informativa**: no recalibra ningún umbral por sí sola.

**Funciones y clases clave.**
- `calibration_table()` — la tabla por tramos (`BUCKETS`); `save_calibration()` — la guarda como informe, una vez por cantidad de
  entradas; `confidence_of()` — la `confianza` del JSON de la respuesta (nunca lanza).

**Invariantes — no romper.** Lleva tasas de acierto: se calcula siempre que se pida, pero se muestra solo con la puerta abierta.
Usa las filas ya cerradas de `memory_entries`.

**Trampas conocidas.** Recalibrar umbrales requiere muestra suficiente y venir **después** del veredicto (decisión del autor).

<!-- hechos:app/memory/calibration.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/calibration.html`](../api/app/memory/calibration.html)
- **Importa (internos):** `app.memory.lessons`, `app.persistence.database`, `app.persistence.repositories.memory_repo`
- **Lo importan:** `app.memory.report`, `app.memory.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `llm_logs`, `memory_entries`
- **Tablas que escribe:** `lessons_reports`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`
<!-- /hechos:app/memory/calibration.py -->

### `app/memory/entries.py`

**Responsabilidad.** Construye la memoria: **una entrada inmutable por operación cerrada y fiable**. La fuente son las sombras
cerradas con PnL (cada señal agrupada tiene una: son la población completa, con o sin ejecución en la cuenta paper).

**Funciones y clases clave.**
- `refresh_entries()` — agrega las sombras cerradas y fiables que todavía no están; `count_closed_reliable()` — solo un conteo
  (no lee PnL); `atr_from_log()` — el ATR(14) % que vio el modelo al decidir.

**Invariantes — no romper.**
- Se excluyen las de reconciliación fallida (mismo criterio que `ai_value`).
- Cada entrada guarda lo que se sabía **al decidir** (instante de cierre de la vela) y el resultado; las noticias y el Fear & Greed
  se leen con el límite de `decision_time` (sin fuga).
- **Lee resultados:** solo corre con `LESSONS_MEMORY_ENABLED` o desde `scripts/lessons_report.py`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/entries.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/entries.html`](../api/app/memory/entries.html)
- **Importa (internos):** `app.memory.features`, `app.news.assets`, `app.persistence.database`, `app.persistence.llm_log_fields`, `app.persistence.models`, `app.persistence.repositories.memory_repo`, `app.persistence.repositories.news_repo`, `app.persistence.repositories.shadow_repo`, `app.persistence.repositories.signals_repo`
- **Lo importan:** `app.api.routes.memory`, `app.memory.report`, `app.memory.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `fear_greed`, `llm_logs`, `memory_entries`, `news_items`, `shadow_trades`, `signals`
- **Tablas que escribe:** `memory_entries`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_phase4_api_routes.py`
<!-- /hechos:app/memory/entries.py -->

### `app/memory/features.py`

**Responsabilidad.** Las características **discretas** de una operación, conocidas al decidir (nunca el resultado), con cortes fijos y
documentados para que los patrones sean comparables en el tiempo.

**Funciones y clases clave.**
- `hour_bin()` (franja de 4 h UTC), `atr_bucket()`, `rr_bucket()`, `risk_bucket()`, `sentiment_bucket()`, `fear_greed_bucket()`
  y `risk_reward()`.
- `patterns_of()` (patrón de una entrada en cada dimensión de `DIMENSIONS`), `patterns_of_query()` y `QueryFeatures`.

**Invariantes — no romper.** Los cortes son **fijos**: cambiarlos rompe la comparabilidad de las entradas ya guardadas. Un dato
desconocido es `UNK`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/features.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/features.html`](../api/app/memory/features.html)
- **Importa (internos):** `app.persistence.repositories.memory_repo`
- **Lo importan:** `app.llm.prompts_v5`, `app.memory.entries`, `app.memory.lessons`, `app.memory.postmortem`, `app.memory.retrieval`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/memory/features.py -->

### `app/memory/gate.py`

**Responsabilidad.** La puerta de los resultados de la memoria: todo lo que lleve PnL, retornos o tasas de acierto pasa por
`results_visible`.

**Funciones y clases clave.**
- `results_visible()` — es `is_outcome_reveal_allowed` y **falla cerrado**; `HIDDEN_RESULTS` — el texto que se muestra en su lugar.

**Invariantes — no romper.** Sin la puerta solo se puede decir **cuántas** operaciones cerradas hay.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/gate.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/gate.html`](../api/app/memory/gate.html)
- **Importa (internos):** `app.core.logging`, `app.persistence.database`, `app.trading.measurement_status`
- **Lo importan:** `app.api.routes.memory`, `app.llm.prompts_v5`, `scripts.lessons_report`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_phase5_base.py`
<!-- /hechos:app/memory/gate.py -->

### `app/memory/lessons.py`

**Responsabilidad.** Las lecciones agregadas: patrones de operaciones cerradas con su tasa de acierto y retorno, y la regla que
decide si una lección puede **influir**.

**Funciones y clases clave.**
- `build_lessons()` — todas las lecciones de una lista de entradas (puro y determinista); `rebuild_lessons()` — las reconstruye
  de toda la memoria bajo un `build_id` nuevo; `wilson_interval()`.

**Invariantes — no romper.** Una lección es `ACTIVE` solo si cumple las **tres** condiciones: (1) muestra mínima
(`LESSONS_MIN_SAMPLE`, 30; si no, `INSUFFICIENT`); (2) el intervalo de Wilson de su tasa de acierto **excluye la tasa base**
(si no, `NOT_SIGNIFICANT`), al **99 %** (z = 2,576) y no al 95 % porque se prueban muchas dimensiones a la vez; (3) el **signo se
mantiene en las dos mitades del tiempo** (si no, `UNSTABLE`).

**Trampas conocidas.** No es una garantía: es un filtro contra patrones que son ruido.

<!-- hechos:app/memory/lessons.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/lessons.html`](../api/app/memory/lessons.html)
- **Importa (internos):** `app.memory.features`, `app.persistence.database`, `app.persistence.repositories.memory_repo`
- **Lo importan:** `app.api.routes.memory`, `app.memory.calibration`, `app.memory.report`, `app.memory.retrieval`, `app.memory.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `memory_entries`
- **Tablas que escribe:** `lessons`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/memory/lessons.py -->

### `app/memory/postmortem.py`

**Responsabilidad.** El post-mortem determinista de una racha perdedora de la cuenta paper: escribe qué tuvieron en común esas
operaciones, **sin LLM**.

**Funciones y clases clave.**
- `build_postmortem()` — analiza la racha actual; `maybe_postmortem()` — guarda **uno** por racha, con clave su primera pérdida.

**Invariantes — no romper.** La pausa automática ya existe: es el circuit breaker del motor de riesgo
(`CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`). El cuerpo lleva PnL: se guarda siempre y se muestra solo con la puerta abierta.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/postmortem.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/postmortem.html`](../api/app/memory/postmortem.html)
- **Importa (internos):** `app.memory.features`, `app.persistence.database`, `app.persistence.repositories.memory_repo`
- **Lo importan:** `app.memory.report`, `app.memory.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `trades`
- **Tablas que escribe:** `lessons_reports`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`
<!-- /hechos:app/memory/postmortem.py -->

### `app/memory/report.py`

**Responsabilidad.** El informe de texto de la memoria de lecciones.

**Funciones y clases clave.**
- `build_report_text()`.

**Invariantes — no romper.** **Solo** se llama con los resultados visibles: lleva tasas de acierto y retornos.
`scripts/lessons_report.py` comprueba la puerta **antes** de abrir la base para escribir; sin ella solo cuenta operaciones.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/report.html`](../api/app/memory/report.html)
- **Importa (internos):** `app.config`, `app.memory.calibration`, `app.memory.entries`, `app.memory.lessons`, `app.memory.postmortem`, `app.persistence.database`, `app.persistence.repositories.memory_repo`
- **Lo importan:** `scripts.lessons_report`
- **Configuración que lee:** `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`, `LESSONS_MIN_SAMPLE`
- **Tablas que lee:** `lessons_reports`, `memory_entries`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/memory/report.py -->

### `app/memory/retrieval.py`

**Responsabilidad.** La recuperación de operaciones **similares ya cerradas** y el contexto de lecciones para una decisión futura
(prompt v5; nada de esto entra al prompt congelado).

**Funciones y clases clave.**
- `find_similar()` — las entradas parecidas a la consulta, cerradas **estrictamente antes** de `as_of`; `similarity()`.
- `build_lessons_context()` — el contexto de lecciones para una decisión, o `None` si los resultados siguen ocultos; registra qué
  lecciones se aplicaron en `lesson_applications`.

**Invariantes — no romper.**
- **Sin fuga temporal**: solo entradas cerradas estrictamente antes de `as_of`.
- **Muestra mínima:** las estadísticas de la cohorte solo con `n >= LESSONS_MIN_SAMPLE`; con menos, evidencia «BAJA» y solo el
  conteo. Los ejemplos concretos se entregan siempre que haya, marcados como anecdóticos.
- **Resultados detrás de la puerta:** antes del registro oficial no hay lecciones, ni siquiera en un prompt.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/retrieval.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/retrieval.html`](../api/app/memory/retrieval.html)
- **Importa (internos):** `app.config`, `app.memory.features`, `app.memory.lessons`, `app.persistence.database`, `app.persistence.repositories.memory_repo`
- **Lo importan:** `app.llm.prompts_v5`
- **Configuración que lee:** `LESSONS_MIN_SAMPLE`, `LESSONS_TOP_K`
- **Tablas que lee:** `lessons`, `memory_entries`
- **Tablas que escribe:** `lesson_applications`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_memory.py`, `tests/unit/test_prompt_v5_prepared.py`
<!-- /hechos:app/memory/retrieval.py -->

### `app/memory/runtime.py`

**Responsabilidad.** La tarea aislada que mantiene la memoria: cada `LESSONS_REBUILD_HOURS` agrega las operaciones cerradas
nuevas, reconstruye las lecciones, guarda un post-mortem si hay racha perdedora y la tabla de calibración.

**Funciones y clases clave.**
- `LessonsRunner.run_once()` / `run_forever()`; `maybe_start_lessons_runner()`, `build_lessons_runner()` (nunca lanza) y
  `run_isolated()`.

**Invariantes — no romper.** Apagada por defecto; mismas garantías que el colector de noticias (construirla no lanza, tarea propia,
nada de lo que le pase llega al resto). **Solo escribe** en las tablas de memoria.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/memory/runtime.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/memory/runtime.html`](../api/app/memory/runtime.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.memory.calibration`, `app.memory.entries`, `app.memory.lessons`, `app.memory.postmortem`, `app.persistence.database`
- **Lo importan:** `app.main`
- **Configuración que lee:** `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`, `LESSONS_MEMORY_ENABLED`, `LESSONS_MIN_SAMPLE`, `LESSONS_REBUILD_HOURS`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/test_circuit_breaker.py`, `tests/e2e/test_no_leaks.py`, `tests/unit/test_memory.py`, `tests/unit/test_memory_isolation.py`
<!-- /hechos:app/memory/runtime.py -->
