# Guía del paquete `api`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/api.html`](../api/app/api.html)

## Qué hace el paquete

La API HTTP de Minerva (FastAPI): **lectura de estado y tres controles; nada de operar**. Ningún endpoint abre ni cierra
posiciones. Todo resultado de la cuenta o de las sombras sale por una **lista blanca** de `serializers.py` y solo cuando
`is_outcome_reveal_allowed` lo permite (la puerta falla cerrada). Solo `GET /health`, `GET /positions` y `GET /trades` son
públicos (además del propio login y logout); el resto de `/api/` exige login. El servidor escucha solo en `127.0.0.1`.

## Zona protegida

**No** para añadir endpoints de solo lectura, pero con una regla que **no se puede relajar**: **toda respuesta que lleve datos de
operaciones debe pasar por la lista blanca de `serializers.py` y por `reveal_allowed`**. Ver
[Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados). Las únicas escrituras de la API son el login, el
logout y las tres acciones de control (que **detienen la medición** si pausan).

## Las 22 rutas

| Ruta | Archivo | Login | Qué devuelve | Resultados |
|---|---|---|---|---|
| `GET /health` | `health.py` | no | vivo, ciclo de señales, control y un bloque por pieza | nunca |
| `GET /positions` | `positions.py` | no | posiciones abiertas de la cuenta paper | sin PnL hasta la puerta |
| `GET /trades` | `trades.py` | no | operaciones de la cuenta paper | sin PnL hasta la puerta |
| `POST /api/auth/login` | `auth.py` | no | abre sesión (cookie `httpOnly`) | — |
| `POST /api/auth/logout` | `auth.py` | no | borra la cookie | — |
| `GET /api/auth/me` | `auth.py` | sí | el usuario de la sesión | — |
| `GET /api/measurement/status` | `measurement.py` | sí | avance de la medición | solo conteos |
| `GET /api/llm/decisions/recent` | `llm_decisions.py` | sí | decisiones del LLM con razonamiento | no son resultados |
| `GET /api/shadow-trades/recent` | `shadow_trades.py` | sí | sombras recientes | sin PnL hasta la puerta |
| `GET /api/news/status` | `news.py` | sí | salud de las fuentes y Fear & Greed | no aplica |
| `GET /api/news/recent` | `news.py` | sí | titulares recientes | no aplica |
| `GET /api/memory/status` | `memory.py` | sí | interruptor y conteos | solo conteos |
| `GET /api/memory/lessons` | `memory.py` | sí | lecciones | vacío hasta la puerta |
| `GET /api/memory/reports` | `memory.py` | sí | informes de post-mortem y calibración | vacío hasta la puerta |
| `GET /api/account` | `account.py` | sí | cuenta paper (posiciones y cerradas) | sin PnL hasta la puerta |
| `GET /api/arm` | `arm.py` | sí | registro del brazo ejecutado | nunca PnL |
| `GET /api/risk` | `risk.py` | sí | estado de riesgo y rechazos | `numbers` es `null` hasta la puerta |
| `GET /api/universe` | `universe.py` | sí | universo vigente y su atribución | no aplica |
| `GET /api/backtest/verdicts` | `backtest.py` | sí | veredictos de la Fase 2 | histórico, no es la medición |
| `GET /api/control/info` | `control.py` | sí | advertencias y palabras de confirmación | — |
| `GET /api/control/audit` | `control.py` | sí | acciones de control recientes | — |
| `POST /api/control/{action}` | `control.py` | sí | pausar, reanudar o kill switch | — |

`tests/unit/test_public_docs.py` comprueba que esta cifra (22) y las rutas citadas son las de la aplicación.

## Cómo se conecta

- `app/main.py` registra los routers y cuelga de `app.state` lo que las rutas necesitan; `deps.py` lo entrega por inyección.
- Los routers de `/api/` declaran `dependencies=[Depends(require_auth)]` **a nivel de router**: una ruta nueva en un router
  existente queda protegida por construcción. La excepción es `auth.py` (sin dependencia de router): el login y el logout son
  públicos y solo `GET /api/auth/me` exige sesión.
- El frontend (`frontend/src/api.ts`) llama a estas rutas; si cambias un contrato, cambia también esa pieza y sus pruebas.

## Archivos

### `app/api/__init__.py`

**Responsabilidad.** Docstring del paquete: el alcance de la API y la regla de la lista blanca.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/api/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api.html`](../api/app/api.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/api/__init__.py -->

### `app/api/deps.py`

**Responsabilidad.** Las dependencias de FastAPI: acceso a los objetos que `lifespan` cuelga en `app.state`.

**Funciones y clases clave.**
- `get_db()`, `get_settings()`, `get_heartbeat()`, `get_scheduler()`, `get_live_labeler()` y los que devuelven `None` cuando el
  objeto falta (`get_paper_arm()`, `get_news_collector()`, `get_push_notifier()`, `get_scheduler_or_none()`,
  `get_paper_backend()`, `get_monitor()`).

**Invariantes — no romper.** Los que pueden devolver `None` existen para que una app mínima (las pruebas) no falle; la ruta debe
tolerarlo.

**Trampas conocidas.** Una dependencia nueva debe leer de `app.state` con `getattr(..., None)` si el objeto es opcional.

<!-- hechos:app/api/deps.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/deps.html`](../api/app/api/deps.html)
- **Importa (internos):** `app.config`, `app.core.heartbeat`, `app.core.scheduler`, `app.news.collector`, `app.persistence.database`, `app.telegram.push`, `app.trading.paper_execution`, `app.trading.shadow_live_labeler`
- **Lo importan:** 16 módulos (api 16)
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_health_route.py`
<!-- /hechos:app/api/deps.py -->

### `app/api/routes/__init__.py`

**Responsabilidad.** Docstring: una ruta por módulo; `app/main.py` las registra.

**Funciones y clases clave.** Ninguna.

**Invariantes.** `require_auth` va declarado en el router, no ruta por ruta.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/api/routes/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes.html`](../api/app/api/routes.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/api/routes/__init__.py -->

### `app/api/routes/account.py`

**Responsabilidad.** La cuenta paper para el dashboard: posiciones abiertas y cerradas con símbolo, lado, estrategia, niveles y horas.

**Funciones y clases clave.**
- `account()` — `GET /api/account`; `results_summary()` — saldo, PnL realizado y flotante, equity y rendimiento.

**Invariantes — no romper.** Sin PnL, precio de salida ni motivo de cierre mientras la puerta esté cerrada: **esos campos ni
siquiera viajan en el JSON y la consulta que lee el PnL ni se ejecuta**. Lista blanca en `serializers.account_trade_to_dict`.

**Trampas conocidas.** Usa el `PaperBackend` (de `app.state`) para el PnL flotante.

<!-- hechos:app/api/routes/account.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/account.html`](../api/app/api/routes/account.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.api.serializers`, `app.config`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.trades_repo`, `app.trading.reveal`
- **Lo importan:** `app.main`
- **Configuración que lee:** `INITIAL_CAPITAL_USDT`, `MAX_SIMULTANEOUS_POSITIONS`
- **Tablas que lee:** `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/api/routes/account.py -->

### `app/api/routes/arm.py`

**Responsabilidad.** El registro del brazo ejecutado: cada decisión APROBADA que el brazo consideró, abierta u omitida, con su motivo.

**Funciones y clases clave.**
- `arm()` — `GET /api/arm`; `REASON_LEGEND` — la leyenda de los motivos.

**Invariantes — no romper.** Nunca PnL; los detalles de cada fila pasan por una lista blanca de claves
(`serializers.safe_arm_details`) porque algunos rechazos de riesgo guardan el equity.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/api/routes/arm.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/arm.html`](../api/app/api/routes/arm.html)
- **Importa (internos):** `app.api.deps`, `app.api.routes.health`, `app.api.security`, `app.api.serializers`, `app.config`, `app.persistence.database`, `app.persistence.repositories.paper_execution_repo`, `app.trading.paper_execution`
- **Lo importan:** `app.main`
- **Configuración que lee:** `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `LLM_REAL_MAX_DELAY_SECONDS`, `real_account_eligible_strategies_list`
- **Tablas que lee:** `paper_execution_log`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/api/routes/arm.py -->

### `app/api/routes/auth.py`

**Responsabilidad.** Login y logout de un único usuario local; solo abre o cierra una sesión.

**Funciones y clases clave.**
- `login()` — `POST /api/auth/login`: cookie `httpOnly`, `SameSite=Strict`; `logout()`; `me()`; `LoginRequest` (usuario y contraseña
  de hasta 128 caracteres).

**Invariantes — no romper.** Responde 503 si `JWT_SECRET` es inseguro (vacío o corto) y 401 ante credenciales inválidas; nunca un 500
por una contraseña de tamaño inválido.

**Trampas conocidas.** No controla el bot: los controles viven en `control.py`.

<!-- hechos:app/api/routes/auth.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/auth.html`](../api/app/api/routes/auth.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.config`
- **Lo importan:** `app.main`
- **Configuración que lee:** `AUTH_PASSWORD_HASH`, `AUTH_USERNAME`, `JWT_SECRET`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_auth.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_phase5_control_api.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_readonly_api_routes.py`
<!-- /hechos:app/api/routes/auth.py -->

### `app/api/routes/backtest.py`

**Responsabilidad.** Los veredictos de backtest por estrategia y si cada una es elegible hoy para la cuenta paper.

**Funciones y clases clave.**
- `verdicts()` — `GET /api/backtest/verdicts`; `verdict_to_dict()`, `parse_reasons()` y `round_reason()` (acota los decimales de los
  motivos de descarte).

**Invariantes — no romper.** Es un **histórico de backtest**, no la medición en vivo: no pasa por la puerta de resultados.

**Trampas conocidas.** Los motivos de descarte traen flotantes crudos (`1.1233661231946566`); por eso se redondean al mostrarlos.

<!-- hechos:app/api/routes/backtest.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/backtest.html`](../api/app/api/routes/backtest.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.config`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.backtest_repo`
- **Lo importan:** `app.main`
- **Configuración que lee:** `real_account_eligible_strategies_list`
- **Tablas que lee:** `backtest_verdicts`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/api/routes/backtest.py -->

### `app/api/routes/control.py`

**Responsabilidad.** Los controles del dashboard: pausar, reanudar y kill switch. Son las **únicas** escrituras de control de la API;
no abren ni cierran posiciones.

**Funciones y clases clave.**
- `execute()` — `POST /api/control/{action}`; `info()` — advertencias y palabras de confirmación; `audit()` — las acciones recientes.
- `require_controls_enabled()`, `require_same_origin()` y `require_json()` — las defensas.

**Invariantes — no romper.** Las defensas, en este orden, cada una con su prueba: (1) login; (2) `DASHBOARD_CONTROLS_ENABLED` (403 si está
apagado); (3) **mismo origen**: una petición con `Origin` o `Sec-Fetch-Site` de otro sitio recibe 403; (4) solo `application/json`
(415); (5) la **palabra de confirmación exacta** (`PAUSAR`, `REANUDAR`, `KILL`; sin ella, 422 y no cambia nada). Cada acción deja su
rastro en `control_audit`.

**Trampas conocidas.** **Pausar o activar el kill switch detiene la medición**; la advertencia es la misma que en Telegram
(`STOPS_MEASUREMENT`). `reanudar` no levanta el stop por drawdown (`resume_drawdown_stop`, a mano).

<!-- hechos:app/api/routes/control.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/control.html`](../api/app/api/routes/control.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.config`, `app.core.logging`, `app.persistence.database`, `app.persistence.repositories.control_audit_repo`, `app.trading.control_actions`
- **Lo importan:** `app.main`
- **Configuración que lee:** `DASHBOARD_CONTROLS_ENABLED`
- **Tablas que lee:** `control_audit`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_check_measurement_safety.py`, `tests/unit/test_phase5_control_api.py`
<!-- /hechos:app/api/routes/control.py -->

### `app/api/routes/health.py`

**Responsabilidad.** `GET /health`: expone dos señales **distintas** para detectar un cuelgue rápido y un bloque por pieza.

**Funciones y clases clave.**
- `health()` — `last_beat_at` (el event loop sigue vivo), `last_cycle_at` (la última evaluación completa del generador de señales), `status`
  (`stale` si `last_cycle_at` es `None` o tiene más de `STALE_AFTER_MINUTES`, 90), `control`, `log_dropped_count` y los bloques
  `llm_labeling`, `paper_execution`, `news` y `telegram_push`.

**Invariantes — no romper.** Sin login (`scripts/telegram_alert.py` lo consulta sin sesión). `llm_labeling.frozen_hash_ok = false` significa que el
etiquetado **no está corriendo** aunque `enabled` sea `true`. Ningún bloque trae PnL, resultados, token ni chat.

**Trampas conocidas.** El umbral de 90 minutos está muy por encima de `SCHEDULER_POLL_SECONDS` (60 s); una pausa a propósito también deja el estado en `stale`.

<!-- hechos:app/api/routes/health.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/health.html`](../api/app/api/routes/health.html)
- **Importa (internos):** 15 módulos (api 1, core 3, llm 2, news 2, persistence 2, telegram 1, trading 4)
- **Lo importan:** `app.api.routes.arm`, `app.api.routes.risk`, `app.main`, `app.telegram.commands`
- **Configuración que lee:** `LLM_LIVE_LABELING_ENABLED`, `REPORT_TIMEZONE`, `TELEGRAM_PUSH_ENABLED`
- **Tablas que lee:** `llm_logs`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_health_route.py`, `tests/unit/test_scheduler_pause.py`
<!-- /hechos:app/api/routes/health.py -->

### `app/api/routes/llm_decisions.py`

**Responsabilidad.** Las decisiones recientes del LLM con su razonamiento, de solo lectura.

**Funciones y clases clave.**
- `recent_decisions()` — `GET /api/llm/decisions/recent`.

**Invariantes — no romper.** Nunca devuelve el `prompt` completo ni el `response_raw`. Sin regla de velado: la decisión y su razonamiento no son un
«resultado de la operación».

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/api/routes/llm_decisions.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/llm_decisions.html`](../api/app/api/routes/llm_decisions.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.llm.schemas`, `app.persistence.database`, `app.persistence.repositories.llm_logs_repo`, `app.persistence.repositories.shadow_repo`
- **Lo importan:** `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `llm_logs`, `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_readonly_api_routes.py`
<!-- /hechos:app/api/routes/llm_decisions.py -->

### `app/api/routes/measurement.py`

**Responsabilidad.** El avance de la medición en vivo, de solo lectura.

**Funciones y clases clave.**
- `status()` — `GET /api/measurement/status`; reutiliza `app.trading.measurement_status`.

**Invariantes — no romper.** Nunca incluye PnL: `MeasurementStatus` no tiene esos campos por diseño.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/api/routes/measurement.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/measurement.html`](../api/app/api/routes/measurement.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.persistence.database`, `app.trading.measurement_status`
- **Lo importan:** `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_readonly_api_routes.py`
<!-- /hechos:app/api/routes/measurement.py -->

### `app/api/routes/memory.py`

**Responsabilidad.** La memoria de lecciones, de solo lectura.

**Funciones y clases clave.**
- `status()` (solo interruptor y conteos), `lessons()` y `reports()`.

**Invariantes — no romper.** `lessons()` y `reports()` devuelven vacío (y el motivo) salvo que `app.memory.gate.results_visible` sea verdadero.

**Trampas conocidas.** Las lecciones llevan un aviso (`DISCLAIMER`): son estadística descriptiva, no una garantía.

<!-- hechos:app/api/routes/memory.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/memory.html`](../api/app/api/routes/memory.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.config`, `app.memory.entries`, `app.memory.gate`, `app.memory.lessons`, `app.persistence.database`, `app.persistence.repositories.memory_repo`
- **Lo importan:** `app.main`
- **Configuración que lee:** `LESSONS_MEMORY_ENABLED`, `LESSONS_MIN_SAMPLE`
- **Tablas que lee:** `lesson_applications`, `lessons`, `lessons_reports`, `memory_entries`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase4_api_routes.py`
<!-- /hechos:app/api/routes/memory.py -->

### `app/api/routes/news.py`

**Responsabilidad.** Las noticias recolectadas, de solo lectura: título, dominio, fecha y enlace (nunca el cuerpo).

**Funciones y clases clave.**
- `status()`, `recent()` y `news_item_to_public_dict()`.

**Invariantes — no romper.** Los titulares son texto **no confiable**: se vuelven a sanear y el enlace solo sale si es `http(s)`. Lleva las atribuciones de las fuentes,
que el dashboard debe mostrar.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/api/routes/news.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/news.html`](../api/app/api/routes/news.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.config`, `app.news.collector`, `app.news.health`, `app.news.sources`, `app.news.text`, `app.persistence.database`, `app.persistence.repositories.news_repo`
- **Lo importan:** `app.main`
- **Configuración que lee:** `news_sources_list`
- **Tablas que lee:** `fear_greed`, `news_items`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase4_api_routes.py`
<!-- /hechos:app/api/routes/news.py -->

### `app/api/routes/positions.py`

**Responsabilidad.** `GET /positions` (sin login): las posiciones abiertas de la cuenta paper.

**Funciones y clases clave.**
- `list_positions()`.

**Invariantes — no romper.** Sin el registro oficial del veredicto no trae PnL, costos ni trailing. No declara `response_model=list[Trade]`.

**Trampas conocidas.** `Trade` declara `pnl_net_usdt` como campo real, así que el filtrado de `response_model` de FastAPI **no protegería nada**.

<!-- hechos:app/api/routes/positions.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/positions.html`](../api/app/api/routes/positions.html)
- **Importa (internos):** `app.api.deps`, `app.api.serializers`, `app.persistence.database`, `app.persistence.repositories.trades_repo`, `app.trading.measurement_status`
- **Lo importan:** `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_account_results_hidden.py`
<!-- /hechos:app/api/routes/positions.py -->

### `app/api/routes/risk.py`

**Responsabilidad.** El estado de riesgo para el dashboard: pausa, kill switch, circuit breaker, stop por drawdown, pérdida diaria, cupos y rechazos recientes.

**Funciones y clases clave.**
- `risk()` — `GET /api/risk`.

**Invariantes — no romper.** Solo estados y conteos de estructura; lo derivado de resultados (racha de pérdidas, contador de cruces del drawdown) va en
`numbers`, que es `null` mientras la puerta esté cerrada. Los **detalles** de los rechazos nunca salen (pueden traer el equity).

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/api/routes/risk.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/risk.html`](../api/app/api/routes/risk.html)
- **Importa (internos):** 14 módulos (api 4, config 1, core 1, llm 1, persistence 4, trading 3)
- **Lo importan:** `app.main`
- **Configuración que lee:** `CIRCUIT_BREAKER_COOLDOWN_HOURS`, `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`, `DRAWDOWN_STOP_MODE`, `MAX_DAILY_LOSS_PCT`, `MAX_DRAWDOWN_PCT`, `MAX_SAME_DIRECTION_POSITIONS`, `MAX_SIMULTANEOUS_POSITIONS`, `REPORT_TIMEZONE`
- **Tablas que lee:** `risk_rejections`, `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/api/routes/risk.py -->

### `app/api/routes/shadow_trades.py`

**Responsabilidad.** Las sombras recientes, de solo lectura.

**Funciones y clases clave.**
- `recent()` — `GET /api/shadow-trades/recent`.

**Invariantes — no romper.** **Nunca** revela el PnL o resultado de una sombra etiquetada hasta que la puerta se abra; el único punto de paso es
`serializers.shadow_trade_to_public_dict`.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/api/routes/shadow_trades.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/shadow_trades.html`](../api/app/api/routes/shadow_trades.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.api.serializers`, `app.persistence.database`, `app.persistence.repositories.shadow_repo`, `app.trading.measurement_status`
- **Lo importan:** `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_readonly_api_routes.py`
<!-- /hechos:app/api/routes/shadow_trades.py -->

### `app/api/routes/trades.py`

**Responsabilidad.** `GET /trades` (sin login): las operaciones de la cuenta paper.

**Funciones y clases clave.**
- `list_trades()`.

**Invariantes — no romper.** Desde la subfase 3.9 esas operaciones incluyen las del brazo ejecutado: su PnL, costos y motivo de cierre solo se ven con
`is_outcome_reveal_allowed`.

**Trampas conocidas.** Igual que `positions.py`: no se usa `response_model`.

<!-- hechos:app/api/routes/trades.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/trades.html`](../api/app/api/routes/trades.html)
- **Importa (internos):** `app.api.deps`, `app.api.serializers`, `app.persistence.database`, `app.persistence.repositories.trades_repo`, `app.trading.measurement_status`
- **Lo importan:** `app.main`
- **Configuración que lee:** ninguno
- **Tablas que lee:** `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_account_results_hidden.py`
<!-- /hechos:app/api/routes/trades.py -->

### `app/api/routes/universe.py`

**Responsabilidad.** El universo vigente para el dashboard: el último snapshot de `asset_universe` (los 30 candidatos con el motivo de cada exclusión), su
antigüedad, los cambios recientes y el último *mark* de cada incluido.

**Funciones y clases clave.**
- `universe()` — `GET /api/universe`; `exclusion_reason()` — por qué un candidato no está incluido, en el mismo orden en que lo decide el refresco.

**Invariantes — no romper.** Lleva la atribución de CoinGecko («Data provided by CoinGecko», enlazada). Son datos de mercado y de configuración:
nada de resultados.

**Trampas conocidas.** Un activo que sale del universo con una posición abierta **no** se cierra por la fuerza (la regla se muestra en la respuesta).

<!-- hechos:app/api/routes/universe.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/routes/universe.html`](../api/app/api/routes/universe.html)
- **Importa (internos):** `app.api.deps`, `app.api.security`, `app.config`, `app.market.attribution`, `app.market.universe`, `app.market.universe_diff`, `app.persistence.database`, `app.persistence.models`, `app.persistence.repositories.system_state_repo`, `app.persistence.repositories.universe_repo`
- **Lo importan:** `app.main`
- **Configuración que lee:** `UNIVERSE_REFRESH_HOURS`, `UNIVERSE_SIZE`, `UNIVERSE_STALENESS_HOURS`
- **Tablas que lee:** `asset_universe`, `system_state`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_read_api.py`
<!-- /hechos:app/api/routes/universe.py -->

### `app/api/security.py`

**Responsabilidad.** La autenticación de un único usuario local: JWT HS256 (`PyJWT`) en una cookie `httpOnly`.

**Funciones y clases clave.**
- `require_auth()` — dependencia que exige sesión válida, declarada en el router entero.
- `create_session_token()` y `decode_session_token()` — el JWT con `sub`, `iat` y `exp` (24 h, `SESSION_DURATION`).
- `hash_password()` / `verify_password()` (bcrypt directo, no `passlib`), `verify_username()` y `jwt_secret_is_safe()` (mínimo
  `MIN_SECRET_LENGTH`, 32 caracteres); `warn_if_jwt_secret_is_unsafe()` avisa una vez al arrancar.

**Invariantes — no romper.**
- Con `JWT_SECRET` vacío o corto todo falla **cerrado**: `login` da 503 y `require_auth` 401 (nunca un 500).
- `decode_session_token` captura la clase base `jwt.PyJWTError`, no solo `InvalidTokenError`: `InvalidKeyError` es subclase directa de `PyJWTError`.
- `verify_username` compara en tiempo constante y sobre **bytes**: `hmac.compare_digest` lanza `TypeError` con un `str` no ASCII (un usuario con tilde
  rompía el login con un 500).
- `verify_password` devuelve `False` (sin lanzar) con hash vacío o con una contraseña de más de 72 bytes (límite de bcrypt).
- Cookie `SameSite=Strict`, `secure=False` (todo corre en loopback HTTP plano; `Secure` rompería la cookie en silencio).

**Trampas conocidas.** Revisión del 2026-10-14: tres correcciones de seguridad cuyo síntoma era un 500 en vez de un 401/503 limpio.

<!-- hechos:app/api/security.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/security.html`](../api/app/api/security.html)
- **Importa (internos):** `app.api.deps`, `app.config`
- **Lo importan:** 14 módulos (api 12, main 1, scripts 1)
- **Configuración que lee:** `JWT_SECRET`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_auth.py`, `tests/unit/test_hash_password_script.py`, `tests/unit/test_phase4_api_routes.py`, `tests/unit/test_phase5_control_api.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_readonly_api_routes.py`
<!-- /hechos:app/api/security.py -->

### `app/api/serializers.py`

**Responsabilidad.** Los serializadores de respuesta: **el único punto de paso para revelar un resultado** de una sombra etiquetada o de la cuenta paper.

**Funciones y clases clave.**
- `shadow_trade_to_public_dict()`, `trade_to_public_dict()` y `account_trade_to_dict()` — por **lista blanca**, con los resultados solo si `reveal_outcome`.
- `safe_arm_details()` / `arm_entry_to_dict()` (una fila de `paper_execution_log` sin PnL) y `rejection_to_dict()` (un rechazo **sin** sus detalles).

**Invariantes — no romper.**
- **Lista blanca explícita, nunca negra:** si `ShadowTrade` gana un campo, **no aparece** hasta que alguien lo agregue a mano. Jamás `trade.model_dump()`.
- Las rutas devuelven `list[dict]` simple, sin `response_model`: como el modelo declara `pnl_net_usdt`, el filtrado de FastAPI no protegería nada.
- Fuera de la lista blanca **siempre** (son señales blandas de resultado): `fee_*`, `funding_paid_usdt`, `slippage_*`, `best_price` y `effective_stop`
  (el trailing revela qué tan favorable fue el precio en una posición abierta).

**Trampas conocidas.** Un campo nuevo que se agregue aquí debe pensarse como una fuga potencial: hay un barrido de pruebas sobre todos los canales.

**Protección.** Sí: [Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados).

<!-- hechos:app/api/serializers.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/api/serializers.html`](../api/app/api/serializers.html)
- **Importa (internos):** `app.persistence.models`
- **Lo importan:** `app.api.routes.account`, `app.api.routes.arm`, `app.api.routes.positions`, `app.api.routes.risk`, `app.api.routes.shadow_trades`, `app.api.routes.trades`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_account_results_hidden.py`, `tests/unit/test_phase5_read_api.py`, `tests/unit/test_serializers.py`
<!-- /hechos:app/api/serializers.py -->
