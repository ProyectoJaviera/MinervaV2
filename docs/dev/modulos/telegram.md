# Guía del paquete `telegram`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/telegram.html`](../api/app/telegram.html)

## Qué hace el paquete

Dos piezas independientes que hablan con **un solo chat privado** por la Bot API de Telegram:

1. **El bot de comandos** (long-polling): `/status`, `/positions`, `/balance`, `/metrics`, `/pause`, `/resume` y `/kill`. Solo
   **informa y controla el proceso**; nunca decide una entrada o una salida.
2. **Las notificaciones push**: avisan cuando se abre o se cierra una posición de la cuenta paper, cuando cambia el universo y
   cuando cambia el estado de riesgo. **Sondean la base de solo lectura**; el núcleo no sabe que existen.

Ninguna de las dos usa la API de Anthropic ni cuesta nada. Ambas están **apagadas por defecto** y solo el bot real (puerto 8000 o
el contenedor) las enciende. **Un fallo de este paquete no puede tumbar ni frenar** el scheduler, el etiquetado ni el monitor.

## Zona protegida

**No** para el código del bot (se puede mejorar), pero con tres reglas que **no se pueden relajar**: (1) nunca mostrar PnL,
balance, equity ni resultados de las sombras mientras la puerta esté cerrada; (2) pausar o `/kill` **detienen la medición**, y la
advertencia es parte del comando; (3) la lista de permitidos del chat. Ver
[Ocultamiento de resultados](../ZONAS_PROTEGIDAS.md#ocultamiento-de-resultados).

## Cómo se conecta

- `app/main.py` llama a `maybe_start_telegram_bot` (con un `BotContext` que reúne la base, el `PaperBackend`, el scheduler, el
  etiquetador, el monitor y el brazo) y a `maybe_start_push_notifier`.
- Los comandos de control llaman a `app/trading/control_actions.py::apply_control`, la misma semántica que el dashboard.
- Un token de bot admite **un solo** consumidor de `getUpdates`: por eso `scripts/run_dev_server.py` fuerza ambos interruptores a
  apagado.

## Archivos

### `app/telegram/__init__.py`

**Responsabilidad.** Docstring del paquete: Telegram solo informa y controla el proceso.

**Funciones y clases clave.** Ninguna.

**Invariantes.** Ninguno propio.

**Trampas conocidas.** Ninguna.

<!-- hechos:app/telegram/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram.html`](../api/app/telegram.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/telegram/__init__.py -->

### `app/telegram/bot.py`

**Responsabilidad.** El bucle de long-polling del bot de comandos.

**Funciones y clases clave.**
- `TelegramBot.run_forever()` — long-polling hasta `stop()` con backoff exponencial ante fallos (de 1 s hasta 300 s) y un WARNING por
  racha de fallos, no uno por intento.
- `is_allowed_update()` — **lista de permitidos estricta**: solo un mensaje de texto de un chat **privado** cuyo `id` coincide
  exactamente con `TELEGRAM_CHAT_ID`.
- `TelegramBot.process_update()` — filtra, responde y nunca propaga una excepción del comando.

**Invariantes — no romper.**
- Todo update de otro chat, usuario o tipo de chat (un grupo con el mismo id incluido) se **ignora en silencio**: ni respuesta ni
  error, para no confirmarle a un tercero que el bot existe.
- **Aislamiento:** toda excepción de una iteración (`Exception`, nunca `BaseException`: la cancelación del cierre ordenado sigue
  funcionando) se captura y se registra **solo por tipo** (nunca la URL ni el token).
- Una excepción en el manejador de un comando se responde con «error interno» (solo al chat permitido) y el bucle sigue; el offset ya
  avanzó, así que ese update no se repite en bucle.
- Al arrancar **descarta lo pendiente** (no ejecuta nada acumulado durante una caída) y los mensajes de más de
  `MAX_MESSAGE_AGE_SECONDS` (120 s).

**Trampas conocidas.** El chat configurado debe ser **privado** (`TELEGRAM_CHAT_ID` positivo); un grupo (id negativo) se ignora.

<!-- hechos:app/telegram/bot.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/bot.html`](../api/app/telegram/bot.html)
- **Importa (internos):** `app.core.logging`, `app.telegram.client`
- **Lo importan:** `app.telegram.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_telegram_bot.py`, `tests/unit/test_telegram_e2e.py`, `tests/unit/test_telegram_runtime.py`
<!-- /hechos:app/telegram/bot.py -->

### `app/telegram/client.py`

**Responsabilidad.** El cliente HTTP asíncrono fino de la Bot API: solo `getUpdates` y `sendMessage`.

**Funciones y clases clave.**
- `TelegramClient.get_updates()` y `send_message()` (texto plano, recortado a 4096 caracteres); `TelegramApiError`.

**Invariantes — no romper.**
- **El token nunca sale de aquí:** la URL lo lleva, así que ninguna excepción de `httpx` se propaga tal cual; se convierte en
  `TelegramApiError` con el **tipo** de la excepción y se corta el encadenamiento (`from None`) para que ni un `logger.exception` lo
  imprima.
- Parámetros verificados en la documentación oficial (`core.telegram.org/bots/api`, 2026-10-17). Un update queda confirmado al
  llamar `getUpdates` con un `offset` mayor que su `update_id`.

**Trampas conocidas.** Un 429 trae `parameters.retry_after`, que se respeta.

<!-- hechos:app/telegram/client.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/client.html`](../api/app/telegram/client.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.telegram.bot`, `app.telegram.push`, `app.telegram.push_runtime`, `app.telegram.runtime`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_push.py`, `tests/unit/test_telegram_bot.py`, `tests/unit/test_telegram_e2e.py`, `tests/unit/test_telegram_runtime.py`
<!-- /hechos:app/telegram/client.py -->

### `app/telegram/commands.py`

**Responsabilidad.** Los comandos: leen el estado y devuelven texto plano (primera línea «MODO PAPER TRADING»).

**Funciones y clases clave.**
- `CommandRouter` — enruta el texto al comando; es el `handler` de `TelegramBot`.
- `cmd_status()` (bot, riesgo, gasto del LLM, medición y brazo ejecutado), `cmd_positions()`, `cmd_balance()`, `cmd_metrics()` y
  `cmd_control()` / `execute_control()` (pausa, reanudar y kill con confirmación de dos pasos).
- `BotContext` — las dependencias de los comandos; `COMMANDS` es la lista oficial (la documentación de uso la contrasta con el código).

**Invariantes — no romper.**
- **Nunca** muestra PnL ni resultados de las **sombras**, ni la comparación APROBADA/RECHAZADA, con el gate abierto o cerrado: de la
  medición solo lee `app.trading.measurement_status` (conteos y marcas de tiempo). No importa `shadow_report`, `ai_value*` ni
  `shadow_repo`.
- `/positions` y `/balance` reflejan la cuenta paper (`trades`), nunca las sombras; desde la subfase 3.9 esa cuenta incluye las
  operaciones del brazo ejecutado, así que balance, equity, PnL flotante y PF/esperanza solo se muestran con
  `is_outcome_reveal_allowed`; antes, solo estructura (símbolo, lado, niveles) y conteos.
- Sin llamadas a la red de Bitunix: el PnL flotante usa los *marks* que ya mantiene el `PositionMonitor`.

**Trampas conocidas.** `/pause` y `/kill` **detienen la medición** (`CONTROL_WARNINGS`); `/resume` levanta la pausa y el kill pero
**no** el stop por drawdown.

<!-- hechos:app/telegram/commands.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/commands.html`](../api/app/telegram/commands.html)
- **Importa (internos):** 14 módulos (api 1, config 1, core 1, llm 1, persistence 5, telegram 1, trading 4)
- **Lo importan:** `app.main`, `app.telegram.runtime`
- **Configuración que lee:** `INITIAL_CAPITAL_USDT`, `LLM_DAILY_BUDGET_USD`, `LLM_LIVE_LABELING_ENABLED`, `MAX_SIMULTANEOUS_POSITIONS`, `REPORT_TIMEZONE`
- **Tablas que lee:** `llm_logs`, `trades`
- **Tablas que escribe:** `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_measurement_uptime.py`, `tests/unit/test_paper_execution_isolation.py`, `tests/unit/test_phase5_base.py`, `tests/unit/test_phase5_control_api.py`, `tests/unit/test_public_docs.py`, `tests/unit/test_telegram_commands.py`, `tests/unit/test_telegram_control.py`, `tests/unit/test_telegram_e2e.py`, `tests/unit/test_telegram_metrics.py`, `tests/unit/test_telegram_runtime.py`
<!-- /hechos:app/telegram/commands.py -->

### `app/telegram/confirmation.py`

**Responsabilidad.** La confirmación de dos pasos de los comandos que cambian el estado (`/pause`, `/resume`, `/kill`).

**Funciones y clases clave.**
- `ConfirmationManager.request()` / `confirm()` — administra UNA solicitud pendiente con ventana de `CONFIRM_WINDOW_SECONDS` (60 s);
  `is_yes()` — exactamente `SI` (con o sin tilde).

**Invariantes — no romper.**
- Solo vale un `SI` precedido por una solicitud **vigente del mismo comando**; pedir otro comando cancela la anterior; la solicitud se
  consume al confirmar.
- Vive en memoria: un reinicio la descarta **a propósito** (un mensaje viejo reprocesado nunca ejecuta nada por sí solo).

**Trampas conocidas.** Reloj monótono inyectable para las pruebas.

<!-- hechos:app/telegram/confirmation.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/confirmation.html`](../api/app/telegram/confirmation.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.telegram.commands`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_base.py`, `tests/unit/test_telegram_control.py`
<!-- /hechos:app/telegram/confirmation.py -->

### `app/telegram/push.py`

**Responsabilidad.** Las notificaciones push de Telegram: avisan al chat permitido de aperturas y cierres de la cuenta paper,
cambios del universo y cambios del estado de riesgo.

**Funciones y clases clave.**
- `PushNotifier.poll_once()` — detecta eventos por **diferencia de estado** y los envía en orden (máximo `MAX_PER_POLL`, 10, por
  sondeo); `run_forever()` toma la línea base y sondea cada `TELEGRAM_PUSH_POLL_SECONDS`.
- `format_open()`, `format_close()` (con `reveal`) y `format_universe()` (lleva la atribución de CoinGecko).
- `risk_snapshot()` y `baseline_state()`; `PushNotifier.health_block()` — el bloque `telegram_push` de `GET /health` (conteos y el
  **tipo** del último error).

**Invariantes — no romper.**
- **Sin ganchos en el núcleo:** nada de `PaperBackend`, el monitor, el brazo, el scheduler ni el motor de riesgo sabe que existe.
  Lo único que escribe es la clave `push_state` de `system_state`.
- **Estado persistido (`push_state`):** al primer arranque toma el estado actual como línea base **sin** notificar el historial; luego
  avanza evento por evento, solo tras un envío correcto o un error permanente.
- **Resultados ocultos:** mientras la puerta (`app.trading.reveal`, falla cerrado) esté cerrada, el cierre dice solo «posición
  cerrada» (símbolo, lado, estrategia y horas); las alertas de riesgo no llevan cifras.
- Envío: texto plano, al menos `MIN_SPACING_S` (1,2 s) entre mensajes, `retry_after` respetado ante un 429, reintentos con espera
  creciente para fallos transitorios y descarte registrado (solo el tipo, nunca el token) para los permanentes (400/401/403/404).

**Trampas conocidas.** Una pausa seguida de una reanudación dentro del mismo intervalo de sondeo no se anuncia (la auditoría sí la
registra). Por diferencia de estado, un cierre retroactivo de la reconciliación también se avisa.

<!-- hechos:app/telegram/push.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/push.html`](../api/app/telegram/push.html)
- **Importa (internos):** 14 módulos (config 1, core 1, market 2, persistence 6, telegram 1, trading 3)
- **Lo importan:** `app.api.deps`, `app.api.routes.health`, `app.telegram.push_runtime`
- **Configuración que lee:** `DRAWDOWN_STOP_MODE`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PUSH_ENABLED`, `TELEGRAM_PUSH_POLL_SECONDS`
- **Tablas que lee:** `asset_universe`, `control_audit`, `system_state`, `trades`
- **Tablas que escribe:** `system_state`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_phase5_push.py`
<!-- /hechos:app/telegram/push.py -->

### `app/telegram/push_runtime.py`

**Responsabilidad.** El arranque **aislado** de las notificaciones push dentro del `lifespan`.

**Funciones y clases clave.**
- `maybe_start_push_notifier()`, `build_push_notifier()` (nunca lanza) y `run_isolated()`.

**Invariantes — no romper.** Apagado por defecto (necesita `TELEGRAM_PUSH_ENABLED=true`, token y chat); construirlo no puede lanzar;
corre en su propia tarea y captura cualquier `Exception`; el servidor de desarrollo y las pruebas nunca escriben al chat.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/telegram/push_runtime.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/push_runtime.html`](../api/app/telegram/push_runtime.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.persistence.database`, `app.telegram.client`, `app.telegram.push`
- **Lo importan:** `app.main`
- **Configuración que lee:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PUSH_ENABLED`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_phase5_push.py`
<!-- /hechos:app/telegram/push_runtime.py -->

### `app/telegram/runtime.py`

**Responsabilidad.** El arranque **aislado** del bot de comandos dentro del `lifespan`.

**Funciones y clases clave.**
- `maybe_start_telegram_bot()`, `build_telegram_bot()` (avisa si falta el token o el chat, o si el chat parece de un grupo) y
  `run_isolated()`.

**Invariantes — no romper.** Apagado por defecto (`TELEGRAM_COMMANDS_ENABLED`, token y chat); el servidor de desarrollo y las pruebas
nunca consumen `getUpdates`; la tarea nunca termina con una excepción que el cierre ordenado tendría que propagar.

**Trampas conocidas.** Un token solo admite un consumidor de long-polling.

<!-- hechos:app/telegram/runtime.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/telegram/runtime.html`](../api/app/telegram/runtime.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.telegram.bot`, `app.telegram.client`, `app.telegram.commands`
- **Lo importan:** `app.main`
- **Configuración que lee:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_COMMANDS_ENABLED`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_telegram_runtime.py`
<!-- /hechos:app/telegram/runtime.py -->
