# Guía de `app/main.py` y `app/__init__.py`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/main.html`](../api/app/main.html)

## Qué hace

`app/main.py` es el punto de entrada (`uvicorn app.main:app`). Construye el objeto FastAPI, registra los routers y define
el `lifespan`: el único lugar donde se **arma** el bot (base de datos, clientes de Bitunix y CoinGecko, backend paper,
sombras, monitor, etiquetador, brazo ejecutado, scheduler, universo, latido y, según los interruptores, Telegram,
notificaciones, noticias y memoria) y donde se **desarma** al parar. La secuencia paso a paso, con todas las tareas asyncio
y cómo se comunican, está en [FLUJO_EJECUCION](../FLUJO_EJECUCION.md).

## Zona protegida

Cambiar **qué se construye o en qué orden** puede alterar la medición (por ejemplo, arrancar el scheduler antes de
reconciliar, o pasar otro `decision_service` al etiquetador). Qué se puede tocar sin riesgo y qué no:
[ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#etiquetador-y-medición). Agregar un router nuevo o un middleware es seguro.

## Cómo se conecta

- Importa a casi todos los paquetes: es la raíz del grafo de dependencias (ver el
  [mapa de paquetes](../README.md#mapa-de-dependencias-entre-paquetes)). Nadie lo importa salvo las pruebas.
- Lo que cuelga de `app.state` (`db`, `paper_execution`, `rest_client`, `paper_backend`, `monitor`, `scheduler`,
  `heartbeat`, `live_labeler`, `settings`, `push_notifier`, `news_collector`, `lessons_runner`) es lo que las rutas
  reciben por inyección en `app/api/deps.py`.
- El dashboard compilado (`frontend/dist`), si existe, se sirve en `/`.

## Archivos

### `app/__init__.py`

**Responsabilidad.** Solo el docstring del paquete raíz: lista los paquetes y apunta al mapa del código.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app.html`](../api/app.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_build_dev_docs.py`, `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:app/__init__.py -->

### `app/main.py`

**Responsabilidad.** Ensamblar la aplicación: `lifespan`, middleware, routers y el montaje del dashboard estático.

**Funciones y clases clave.**
- `lifespan(app)` — arranque en este orden: logging → aviso si `JWT_SECRET` es débil → base de datos
  (`Database.connect()`, que crea tablas y migra) → `BitunixRestClient`, `PaperBackend`, `ShadowBook`, `PositionMonitor`,
  `BitunixPublicWSClient` (con `monitor.handle_ws_message` como callback) → `PaperExecutionArm` → `ShadowLiveLabeler`
  (con el brazo como `on_decision`) → `Scheduler` → `UniverseRefresher` → `Heartbeat` → **reconciliación al arrancar**
  (`monitor.reconcile_on_startup()`) → **activación del brazo** (`paper_arm.activate()`) → `monitor.start_feed()` →
  suscripciones del WebSocket → `app.state` → tareas permanentes (WebSocket, monitor, scheduler, universo, latido) →
  tareas opcionales (Telegram, push, noticias, memoria).
- Al parar (`finally`): pide `stop()` a cada pieza, **cancela** las tareas y las espera, drena el etiquetador
  (`live_labeler.shutdown()`) y el brazo (`paper_arm.shutdown()`), cierra los clientes y la base, y apaga el logging.
- `add_paper_trading_header` — middleware que pone `X-Minerva-Mode: PAPER_TRADING` en **toda** respuesta.
- Routers: públicos `health`, `positions`, `trades` y `auth` (el login y el logout); con login `measurement`, `llm_decisions`,
  `shadow_trades`, `news`, `memory`, `account`, `arm`, `risk`, `universe`, `backtest` y `control`.
- El montaje de `frontend/dist` (`StaticFiles`, `html=True`) va **al final**.

**Invariantes — no romper.**
- **Solo paper:** `main.py` no importa ni instancia `BitunixBackend`; el único backend que construye es `PaperBackend`
  (lo comprueba `tests/unit/test_paper_execution_isolation.py`).
- La reconciliación va **antes** de abrir el feed y de arrancar el scheduler: el periodo caído se reproduce con velas de
  1 minuto antes de que entren ticks nuevos.
- Un fallo de la reconciliación o de la activación del brazo se registra (CRITICAL) y **no impide arrancar**: la
  medición no puede depender de ellos. Si el brazo falla, queda con `active = False`.
- Las tareas opcionales nunca lanzan ni impiden arrancar el resto (`maybe_start_*` capturan todo).
- El montaje estático va **después** de todos los routers: Starlette resuelve las rutas en el orden en que se agregan y
  el montaje en `/` captura todo lo que no resolvió antes.
- Las rutas del dashboard comparten una única dependencia de login; una ruta nueva en un router existente queda
  protegida por construcción (ver [api](api.md)).

**Trampas conocidas.**
- `settings` se instancia al importar `app.config`, así que `app.main` hereda el entorno y el directorio de trabajo del
  proceso: `scripts/run_dev_server.py` fija las variables **antes** de importar `app` por esta razón.
- `frontend/dist` es opcional a propósito: un checkout que nunca corrió `npm --prefix frontend run build` arranca igual,
  solo sin dashboard.
- El orden de apagado importa: primero `stop()` y cancelar las tareas, **después** drenar etiquetador y brazo (que pueden
  tener llamadas al LLM en vuelo) y al final cerrar la base. Una tarea que escribe en una base cerrada falla.

<!-- hechos:app/main.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/main.html`](../api/app/main.html)
- **Importa (internos):** 35 módulos (api 16, config 1, core 3, execution 1, market 4, memory 1, news 1, persistence 1, telegram 3, trading 4)
- **Lo importan:** ninguno
- **Configuración que lee:** `BITUNIX_RATE_LIMIT_PER_SEC`, `BITUNIX_REST_BASE_URL`, `BITUNIX_WS_PUBLIC_URL`, `COINGECKO_API_KEY`, `COINGECKO_BASE_URL`, `DATABASE_PATH`, `HEARTBEAT_SECONDS`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** 13 archivos (e2e 2, unit 11)
<!-- /hechos:app/main.py -->
