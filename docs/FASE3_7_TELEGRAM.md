# Subfase 3.7: bot de Telegram por long-polling

Implementa el punto 5 de `docs/FASE3_PLAN.md` con los ajustes que impone la medición en
vivo (plan aprobado el 2026-10-17). Telegram **informa y controla el proceso**; nunca
decide entradas ni salidas.

## Qué hace

Código en `app/telegram/` (`client.py`, `bot.py`, `commands.py`, `confirmation.py`,
`runtime.py`). Texto plano, primera línea "MODO PAPER TRADING".

| Comando | Qué responde |
|---|---|
| `/status` | capital, balance y equity de la **cuenta real simulada**, posiciones, kill switch, **pausa**, circuit breaker, stop por drawdown, gasto de LLM de hoy vs tope, último ciclo de señales, `frozen_hash_ok`/etiquetado, y una línea de avance de la medición |
| `/positions` | posiciones abiertas de la cuenta real simulada con PnL flotante (usa los marks del monitor, sin red) |
| `/balance` | capital inicial, balance realizado y equity de la cuenta real simulada |
| `/metrics` | **solo avance** de la medición (x/300 por lado, tasa de aprobación y banda, no-OK, costo, ritmo/ETA **por calendario y EN MARCHA**, con el porcentaje de tiempo en marcha) + métricas de la cuenta real; **con el gate cerrado y también abierto** (decisión del 2026-10-17). Gate abierto añade el puntero a `docs/VEREDICTO_3_6.md` |
| `/pause` | pausa el scheduler (persistente) |
| `/kill` | kill switch **y** pausa del scheduler |
| `/resume` | levanta pausa y kill switch (no el stop por drawdown) |

`/pause`, `/kill` y `/resume` piden confirmación: responden con la advertencia y hay que
repetir el comando con `SI` dentro de 60 s (`/pause SI`); solo vale un `SI` precedido por
una solicitud vigente del **mismo** comando, hay una sola solicitud pendiente a la vez, se
consume al usarla y un reinicio la descarta. Cada acción ejecutada queda en el log y en
`system_state.telegram_last_control_action`.

## Reglas de seguridad (cada una con test)

- **Pausar DETIENE LA MEDICIÓN**: sin señales, sin sombras y sin etiquetado del LLM
  (`Scheduler.run_cycle` es lo que los dispara). Los cierres de posiciones y el motor de
  riesgo siguen. La pausa **persiste** entre reinicios (`system_state.scheduler_paused`),
  así que `/health` gana `control: {paused, kill_switch_active}` y el resumen diario avisa;
  mientras dure, `last_cycle_at` no avanza y `/health` queda `stale` (la alerta externa
  avisa: no puede ser silenciosa). El kill switch del motor de riesgo, por sí solo, solo
  bloquea entradas reales.
- **Nunca PnL ni resultados de sombras ni la comparación APROBADA/RECHAZADA** por
  Telegram. `commands.py` no importa `shadow_report`, `ai_value*` ni `shadow_repo` (test
  estructural) y sus consultas sobre `shadow_trades` son las de `measurement_status`, sin
  PnL (test con traza de SQL). El veredicto sale solo de `scripts/ai_value_report.py`,
  después del placebo.
- **Allowlist estricta**: solo se procesa un mensaje de un chat **privado** con id
  exactamente igual a `TELEGRAM_CHAT_ID`; cualquier otro (incluido un grupo con ese id) se
  ignora **en silencio**, sin respuesta. Al arrancar se descarta lo pendiente (nada viejo
  se ejecuta) y se ignoran mensajes de más de 120 s.
- **Aislamiento**: tarea propia, `except Exception` por iteración con backoff exponencial
  (1 s a 5 min), un WARNING por racha, tiempos de espera explícitos, cierre del cliente en
  `finally`; `run_isolated` impide que un bug del bot propague nada; construirlo nunca
  impide arrancar el sistema. Pruebas: con el cliente fallando de todas las formas (401,
  409, 429, red, JSON, excepción) un `Scheduler.run_forever` real, el etiquetado y un
  monitor siguen avanzando y el event loop no se bloquea. El token jamás aparece en logs
  ni excepciones.
- **Apagado por defecto**: `TELEGRAM_COMMANDS_ENABLED=false`. `start_bot.ps1` lo fija en
  `true` **solo con `-Port 8000`**; `run_dev_server.py` lo fuerza `false` y vacía el token
  (un token solo admite **un** consumidor de `getUpdates`).

## Verificado en la documentación oficial de Telegram (2026-10-17)

`getUpdates` (`offset`, `limit` 1-100, `timeout`, `allowed_updates`; un update se confirma
al pedir con `offset` mayor), campos `Update`/`Message`/`Chat`, `sendMessage`
(`chat_id`, `text`; 4096 caracteres), límites (≤1 mensaje/s por chat; 429 al excederlos).
**No verificado** (por eso el bot trata todo no-OK con backoff sin depender de ello): el
código exacto ante dos consumidores de `getUpdates` (409), `retry_after` en 429, y
`link_preview_options` vs `disable_web_page_preview` (las respuestas no llevan URLs).

## Despliegue (reinicia el bot real; lo haces tú)

Ventana: **entre dos cierres de vela horarios**, de **:10 a :40** de cualquier hora (el
cierre llega en :00 y el etiquetado tarda ~20-50 s: una tarea en vuelo se pierde si paras
dentro de los primeros minutos y esa sombra queda sin fila). El arranque tarda 1-3 min
(backfill). Evita además los cierres de 4 h (00/04/08/12/16/20 UTC = 21/01/05/09/13/17
hora de Chile en verano).

1. **Antes**: `curl http://127.0.0.1:8000/health` → `status: ok`, `frozen_hash_ok: true`,
   `llm_labeling.enabled: true`; anota `last_cycle_at`. `git log -1` debe ser el commit a
   desplegar. `.env` ya tiene `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (los de la alerta).
2. **Parar**: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1`
   (confirma que el puerto 8000 quedó libre).
3. **Arrancar**: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1`
   (rehúsa si el puerto está ocupado; espera `/health` hasta 180 s).
4. **Verificar `frozen_hash_ok` y el etiquetado**: `/health` → `status: ok`,
   `llm_labeling.frozen_hash_ok: true`, `enabled: true`, `control.paused: false`,
   `last_cycle_at` avanzando; `logs\medicion_out.log` sin "Etiquetado en vivo DETENIDO" y
   con "Bot de Telegram iniciado". Tras el **siguiente cierre horario** (con señal, una
   sombra nueva): `.\.venv\Scripts\python.exe scripts\telegram_daily.py --dry-run`; sin
   señales basta con que `last_cycle_at` avance y el hash siga en true.
5. **Telegram** (smoke manual, requiere el token real): desde tu chat `/status` y
   `/metrics` deben responder; `/pause` debe advertir que detiene la medición (no
   confirmes, o confirma y `/resume` enseguida); desde otra cuenta el bot no responde nada.
6. **Reversa**: pon `TELEGRAM_COMMANDS_ENABLED` en `false` en `start_bot.ps1` y repite
   2-3, o vuelve al commit anterior y repite; una pausa persistente se levanta con
   `/resume`.

## Fuera de alcance

Sin comandos que abran o cierren posiciones; sin PnL (eso es el reporte de paso a dinero
real, punto 8); sin webhook; `/resume` no levanta el stop por drawdown.
