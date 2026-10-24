# Guía del paquete `core`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/core.html`](../api/app/core.html)

## Qué hace el paquete

Es el núcleo transversal: el bucle del scheduler que genera señales cada ciclo, el logging que nunca bloquea, el latido
del proceso y el cliente HTTP que usa todo el proyecto. Casi todo lo demás depende de `core`; `core` solo depende de
`app.config` y, el scheduler, de los paquetes que orquesta.

## Zona protegida

**Solo `scheduler.py`** está en una zona protegida: decide qué señales nacen y cuándo se etiquetan, así que cambiar su
orden de pasos cambia la medición. Ver [ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#agrupación-de-señales-y-ciclo-del-scheduler).
`logging.py`, `heartbeat.py` y `http.py` se pueden tocar con seguridad (sin cambiar lo que se registra de la medición).

## Cómo se conecta

- `app/main.py` crea el `Scheduler` y el `Heartbeat` en el `lifespan` y los corre como tareas asyncio permanentes
  (ver [FLUJO_EJECUCION](../FLUJO_EJECUCION.md)).
- `GET /health` (`app/api/routes/health.py`) lee `Scheduler.last_cycle_at`, `Heartbeat.last_beat_at` y
  `get_dropped_log_count()`.
- Todos los clientes de red (Bitunix, CoinGecko, Telegram, noticias) se construyen con `build_async_http_client()`.

## Archivos

### `app/core/__init__.py`

**Responsabilidad.** Solo el docstring del paquete.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/core/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/core.html`](../api/app/core.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:app/core/__init__.py -->

### `app/core/heartbeat.py`

**Responsabilidad.** Prueba que el event loop sigue girando, independientemente de cualquier lógica de negocio: cada
`HEARTBEAT_SECONDS` (por defecto 300 s) escribe una línea «proceso vivo» en el log y guarda el instante.

**Funciones y clases clave.**
- `Heartbeat` — la tarea. `beat()` fija `last_beat_at` y escribe la línea (con el contador de logs descartados);
  `run_forever()` late al arrancar y luego cada intervalo hasta `stop()`.

**Invariantes.**
- El latido **no** depende del scheduler ni del monitor: si deja de avanzar es que algo bloqueó el event loop. Por eso
  `/health` expone `last_beat_at` y `last_cycle_at` por separado y nunca debe mezclarlos.

**Trampas conocidas.** Nació del incidente de estabilidad del 2026-10-11 (el bot quedó congelado unas 4 horas sin que
nada lo notara); la causa raíz está en `logging.py`.

<!-- hechos:app/core/heartbeat.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/core/heartbeat.html`](../api/app/core/heartbeat.html)
- **Importa (internos):** `app.core.logging`
- **Lo importan:** `app.api.deps`, `app.api.routes.health`, `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_health_route.py`, `tests/unit/test_heartbeat.py`, `tests/unit/test_scheduler_pause.py`
<!-- /hechos:app/core/heartbeat.py -->

### `app/core/http.py`

**Responsabilidad.** Es el **único** lugar donde se construye un cliente HTTP asíncrono: `build_async_http_client()`.

**Funciones y clases clave.**
- `build_async_http_client(timeout)` — devuelve un `httpx.AsyncClient` que usa `truststore` (el almacén de certificados
  del sistema operativo, no el paquete `certifi`) y tiene el keep-alive desactivado (`NO_KEEPALIVE_LIMITS`).

**Invariantes.**
- Ningún módulo debe instanciar `httpx.AsyncClient` directamente: se perdería la compatibilidad con redes con
  inspección TLS y el keep-alive apagado.

**Trampas conocidas.**
- *Keep-alive apagado:* en la corrida real de la Fase 2 una conexión persistente reutilizada quedó colgada esperando
  una respuesta que nunca llegó, y ni el timeout de httpx ni `asyncio.wait_for` la cortaban. Una conexión nueva por
  petición cuesta ~20-50 ms más pero elimina la clase de problema.
- *Docker:* `truststore` lee el almacén de **la imagen**, que no tiene el certificado raíz de una inspección TLS
  corporativa; si el bot corre detrás de un proxy así, el certificado se instala al construir la imagen (carpeta
  `docker/certs/`). Sin eso las llamadas fallan con `CERTIFICATE_VERIFY_FAILED` dentro del contenedor aunque funcionen
  en el equipo anfitrión.

<!-- hechos:app/core/http.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/core/http.html`](../api/app/core/http.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.market.bitunix_rest`, `app.market.coingecko_client`, `app.news.fetch`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/core/http.py -->

### `app/core/logging.py`

**Responsabilidad.** Configura el logging de modo que **ninguna llamada a `logger.info(...)` pueda bloquear el event
loop**: cada registro se encola sin esperar jamás y dos hilos independientes (archivo y consola) lo escriben.

**Funciones y clases clave.**
- `setup_logging()` — idempotente; crea `minerva.log` (rotativo, 10 MB × 5 respaldos, en `LOG_DIR`), las dos colas
  y los dos `QueueListener`, y limpia los handlers síncronos que uvicorn instaló antes del `lifespan`.
- `BoundedQueueHandler` — su `enqueue()` es siempre `put_nowait`; con la cola llena descarta DEBUG/INFO (y los cuenta) y
  solo pierde un WARNING o más grave si la cola ya está llena de WARNING o más graves.
- `_UniqueSentinelQueueListener` y `_stop_listener()` — apagado seguro: el centinela de parada es un objeto propio
  porque `enqueue` puede desalojar cualquier cosa de la cola; la consola se cierra con timeout.
- `shutdown_logging()`, `get_dropped_log_count()` y `get_logger()` — apagado ordenado, contador de descartes (va a
  `/health` y al latido) y acceso al logger de un módulo.

**Invariantes.**
- **Nunca loguear una credencial** (`settings.anthropic_api_key`, `settings.telegram_bot_token`...): este módulo no
  imprime valores de configuración y quien agrega logs debe respetarlo.
- `BoundedQueueHandler.handleError` no escribe a stderr (podría ser el mismo pipe trabado del incidente).
- El archivo es el destino **principal**; la consola es best-effort.

**Trampas conocidas.**
- *Incidente del 2026-10-11:* la consola quedó pausada fuera del repo, el `write()` síncrono de `logging.basicConfig`
  bloqueó el hilo único de asyncio y el bot se congeló ~4 h. De ahí las dos colas y los dos hilos.
- uvicorn configura **sus propios** handlers síncronos antes de que corra el `lifespan`; si no se quitan
  (`_detach_uvicorn_handlers`), un log de acceso escribe directo y puede reproducir el bloqueo.
- Los logs de nivel INFO siguen mostrando el motivo de cierre de las sombras y el PnL de las posiciones paper (ver
  «Limitaciones conocidas» de [USO](../../USO.md#limitaciones-conocidas)); no se tocaron para no alterar el comportamiento en medición.
- `httpx` se fija en WARNING: a INFO registraría cada petición a la API.

<!-- hechos:app/core/logging.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/core/logging.html`](../api/app/core/logging.html)
- **Importa (internos):** `app.config`
- **Lo importan:** 35 módulos (api 2, backtesting 2, core 2, execution 1, llm 1, main 1, market 7, memory 2, news 3, scripts 3, telegram 5, trading 6)
- **Configuración que lee:** `LOG_DIR`, `LOG_LEVEL`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_analyze_risk_script.py`, `tests/unit/test_logging.py`, `tests/unit/test_universe_refresh.py`
<!-- /hechos:app/core/logging.py -->

### `app/core/scheduler.py`

**Responsabilidad.** El bucle principal del bot. Cada `SCHEDULER_POLL_SECONDS` corre un ciclo: genera las señales de
todas las estrategias sobre el universo vigente, abre una **sombra** por cada candidato, dispara el etiquetado en vivo
con el LLM y mantiene las suscripciones del WebSocket.

**Funciones y clases clave.**
- `Scheduler.run_forever()` — ejecuta `run_cycle()` y espera; una excepción del ciclo se registra y el bucle sigue.
- `Scheduler.run_cycle()` — el ciclo: (1) si el scheduler está **pausado** devuelve `[]` sin avanzar `last_cycle_at`;
  (2) avisa al etiquetador (`note_cycle_start`, que comprueba el hash del prompt); (3) `run_signal_generation_cycle`;
  (4) por cada candidato abre la sombra (`ShadowBook.open_candidate`), dispara `maybe_label(shadow_trade, candidate)`
  sin esperar y, solo con `AUTO_OPEN_WITHOUT_LLM`, intenta abrir en la cuenta paper; (5) `sync_ws_subscriptions()`;
  (6) fija `last_cycle_at`.
- `Scheduler._process_candidate()` — la apertura automática sin LLM; con `AUTO_OPEN_WITHOUT_LLM=false` (el valor para
  siempre) devuelve `None` de inmediato. Una señal contraria con posición abierta se ignora: nunca cierra ni invierte.
- `Scheduler.sync_ws_subscriptions()` — suscribe el feed al universo vigente más los símbolos con posición abierta.

**Invariantes.**
- El scheduler **solo abre**; los cierres (SL, TP, trailing, liquidación, manual) los hace el `PositionMonitor`.
- Las aperturas con LLM **no** pasan por aquí: las hace el brazo ejecutado (`app/trading/paper_execution.py`) cuando el
  etiquetador guarda una APROBADA.
- `maybe_label` es fuego y olvido: un fallo del etiquetado jamás debe tumbar el ciclo ni la apertura de sombras.
- Un error en una sombra o en un candidato se captura y se registra; el resto del ciclo continúa.
- Pausar **detiene la medición** (no hay señales, sombras ni etiquetado), por eso `last_cycle_at` no avanza y `/health`
  pasa a `stale`: una pausa nunca puede ser silenciosa.

**Trampas conocidas.**
- `shadow_repo.mark_executed` solo se llama cuando la ruta `AUTO_OPEN_WITHOUT_LLM` abre una posición; en la operación
  normal la columna `executed_in_real_account` de las sombras queda en 0 y el vínculo sombra → trade está en
  `paper_execution_log`.
- `last_cycle_at` es el fin del último ciclo **completo**; no es el latido (ver `heartbeat.py`).

<!-- hechos:app/core/scheduler.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/core/scheduler.html`](../api/app/core/scheduler.html)
- **Importa (internos):** 14 módulos (config 1, core 1, execution 1, market 2, persistence 4, trading 5)
- **Lo importan:** `app.api.deps`, `app.api.routes.health`, `app.api.routes.risk`, `app.main`
- **Configuración que lee:** `AUTO_OPEN_WITHOUT_LLM`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `SCHEDULER_POLL_SECONDS`
- **Tablas que lee:** `asset_universe`, `trades`
- **Tablas que escribe:** `shadow_trades`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_memory_isolation.py`, `tests/unit/test_news_runtime_isolation.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_phase5_push.py`, `tests/unit/test_scheduler.py`, `tests/unit/test_scheduler_pause.py`, `tests/unit/test_shadow_book.py`, `tests/unit/test_telegram_runtime.py`
<!-- /hechos:app/core/scheduler.py -->
