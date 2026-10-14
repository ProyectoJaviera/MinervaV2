r"""Alerta externa de caida via Telegram (pre-Fase 5, item 1 del plan aprobado
2026-10-14): sondea `GET /health` (`scripts/check_health.py`, reusado tal cual,
sin duplicar su logica de HTTP/parseo) y avisa por Telegram si el bot real
(puerto 8000) esta STALE o SIN_RESPUESTA. Pensado para correr desde el
Programador de tareas de Windows cada 10 minutos, en un proceso COMPLETAMENTE
separado del bot -- si el bot entero se cuelga, este script sigue pudiendo
avisar porque nunca depende de su propio event loop.

Solo avisa en un CAMBIO de estado (incluida la recuperacion a OK) y, mientras
sigue igual y caido, con un recordatorio cada `--reminder-minutes` (default
60) -- nunca en silencio mientras algo esta roto, nunca spam mientras todo
esta OK. El estado (ultimo status visto, desde cuando esta caido, cuando se
aviso por ultima vez) vive en un JSON chico (`--state-file`, default
`data/telegram_alert_state.json` -- `data/` ya esta completo en
`.gitignore`). Ese archivo SOLO se actualiza tras un envio exitoso a Telegram:
si el envio falla, el proximo poll (10 min despues) reintenta el aviso
pendiente en vez de darlo por hecho y perderlo.

Token y chat_id vienen de `settings.telegram_bot_token`/`telegram_chat_id`
(ya existian vacios en `app/config.py`/`.env.example`) -- nunca se imprimen
ni se loguean aqui, ni siquiera en un mensaje de error.

Como crear el bot y obtener el chat_id (una sola vez, el usuario, ENTERAMENTE
fuera de este script y de Claude):
1. En Telegram, habla con `@BotFather`, envia `/newbot` y segui el flujo
   (nombre + username terminado en "bot"). Te da el token.
2. Desde la cuenta donde quieras recibir los avisos, mandale CUALQUIER
   mensaje al bot nuevo (p. ej. `/start`).
3. Abri en el navegador `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   y lee `result[0].message.chat.id` -- ese numero es el chat_id.
4. Completa `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` en tu `.env`.

Probalo primero a mano en consola:

    python scripts/telegram_alert.py

El script fija su propio directorio de trabajo al arrancar (ver el
`os.chdir` mas abajo), asi que funciona igual sin importar desde donde lo
dispare el Programador de tareas (tipicamente System32, no la raiz del
repo) -- lo unico que SI debe ser una ruta absoluta es el `/TR` de abajo
(schtasks lo exige). Tarea de Windows cada 10 min, sin ventana de consola
(probar primero con python.exe para ver errores, recien despues apuntar a
pythonw.exe). Corre esto desde cmd.exe parado en D:\Estudio\IA\Minerva: el
`%cd%` se expande ANTES de que schtasks lo vea (verificado), asi que el
comando que queda guardado en la tarea ya tiene la ruta absoluta de verdad:

    schtasks /Create /SC MINUTE /MO 10 /TN "MinervaHealthAlert" /ST 00:00 /F ^
        /TR "%cd%\.venv\Scripts\pythonw.exe %cd%\scripts\telegram_alert.py"
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

# El Programador de tareas de Windows arranca scripts con el directorio de
# trabajo en System32 (o el que sea), NO en la raiz del repo -- antes de esta
# linea, `sys.path.insert(0, ".")` resolvia a ESE directorio ajeno y rompia
# el import de abajo con ModuleNotFoundError (reproducido 2026-10-14: el
# unico sintoma visible era la tarea fallando en el historial del
# Programador, sin mas detalle). Se fija el cwd a la raiz del repo (dos
# niveles arriba de este archivo) ANTES de insertar "." en sys.path o de
# importar nada de app/scripts, para que ese "." ya apunte a donde debe.
os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from scripts.check_health import OK, SIN_RESPUESTA, STALE  # noqa: E402
from scripts.check_health import main as check_health_main  # noqa: E402

STATUS_NAMES = {OK: "OK", STALE: "STALE", SIN_RESPUESTA: "SIN_RESPUESTA"}
DEFAULT_STATE_FILE = "data/telegram_alert_state.json"
DEFAULT_REMINDER_MINUTES = 60.0


_EMPTY_STATE = {"last_status": None, "down_since": None, "last_notified_at": None}


def _load_state(path: Path) -> dict:
    """Un archivo corrupto o ilegible (disco lleno a mitad de una escritura
    anterior, edicion manual rota, lo que sea) se trata como "sin estado" en
    vez de tumbar el script -- perder un dia de historial de cuando empezo
    una caida es mucho menos grave que dejar de avisar del todo porque el
    archivo de estado se daño."""
    if not path.exists():
        return dict(_EMPTY_STATE)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return dict(_EMPTY_STATE)


def _save_state(path: Path, state: dict) -> None:
    """Escritura atomica (archivo `.tmp` + `os.replace`): si el proceso se
    interrumpe a mitad de camino (corte de energia, kill), el peor caso es
    que el `.tmp` quede huerfano -- `state.json` nunca queda truncado o a
    medio escribir, que es lo que `_load_state` tendria que tolerar si no."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp_path, path)


def _should_notify(
    state: dict, current_status_name: str, now: datetime, reminder_minutes: float
) -> bool:
    """Avisa en CUALQUIER cambio de estado (incluida la recuperacion a OK, y un
    cambio de causa STALE<->SIN_RESPUESTA aunque siga "caido"), y mientras
    sigue igual y caido, cada `reminder_minutes` desde el ultimo aviso. Nunca
    avisa si sigue igual y OK (el caso comun, sin trafico a Telegram)."""
    # Sin estado previo (primera corrida, o se borro el archivo) se trata
    # como si el ultimo estado visto fuera OK: asi un arranque en frio con el
    # bot YA bien no avisa nada (no hay cambio real), pero un arranque en
    # frio con el bot YA caido si avisa de inmediato (ver mas abajo).
    last_status = state.get("last_status") or "OK"
    if current_status_name != last_status:
        return True
    if current_status_name == "OK":
        return False
    last_notified_at = state.get("last_notified_at")
    if last_notified_at is None:
        return True
    elapsed = now - datetime.fromisoformat(last_notified_at)
    return elapsed >= timedelta(minutes=reminder_minutes)


def _next_down_since(state: dict, current_status_name: str, now: datetime) -> str | None:
    """Preserva el inicio de la racha de caida a traves de un cambio de causa
    (STALE<->SIN_RESPUESTA): solo se reinicia al arrancar una racha nueva
    (desde OK o desde el estado inicial sin archivo) o al recuperarse."""
    if current_status_name == "OK":
        return None
    if state.get("last_status") in (None, "OK"):
        return now.isoformat()
    return state.get("down_since") or now.isoformat()


def _build_message(
    current_status_name: str, check_output: str, old_down_since: str | None, now: datetime
) -> str:
    if current_status_name == "OK":
        desde = f" (estuvo caido desde {old_down_since})" if old_down_since else ""
        return f"Minerva (puerto 8000) volvio a OK{desde} -- {now.isoformat()}\n{check_output}"
    return f"Minerva (puerto 8000) esta {current_status_name} -- {now.isoformat()}\n{check_output}"


def send_telegram_message(token: str, chat_id: str, text: str, *, timeout: float = 10.0) -> bool:
    """True si Telegram confirmo el envio (HTTP 200). Nunca loguea `token` ni
    `chat_id`, ni siquiera en un mensaje de error."""
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=timeout,
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - cualquier falla de red/HTTP cuenta como "no enviado"
        print(f"No se pudo avisar por Telegram: {type(exc).__name__}")
        return False


def run(
    *,
    host: str,
    port: int,
    timeout: float,
    state_path: Path,
    reminder_minutes: float,
    token: str,
    chat_id: str,
    now: datetime | None = None,
    check_health_fn: Callable[[str, int, float], int] = check_health_main,
    send_fn: Callable[..., bool] = send_telegram_message,
) -> bool:
    """Devuelve si el PIPELINE DE ALERTADO funciono -- no el estado del bot.
    STALE/SIN_RESPUESTA correctamente detectado y avisado es `True` (el
    monitor hizo su trabajo); solo un envio a Telegram fallido (cuando hacia
    falta avisar) es `False`, para que el Programador de tareas marque esa
    corrida como fallida en su historial."""
    now = now or datetime.now(UTC)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        status_code = check_health_fn(host, port, timeout)
    check_output = buffer.getvalue().strip()
    print(check_output)
    current_status_name = STATUS_NAMES[status_code]

    state = _load_state(state_path)
    if not _should_notify(state, current_status_name, now, reminder_minutes):
        return True

    message = _build_message(current_status_name, check_output, state.get("down_since"), now)
    if not send_fn(token, chat_id, message, timeout=timeout):
        return False  # no se actualiza el estado: el proximo poll reintenta el aviso pendiente

    _save_state(state_path, {
        "last_status": current_status_name,
        "down_since": _next_down_since(state, current_status_name, now),
        "last_notified_at": now.isoformat(),
    })
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--reminder-minutes", type=float, default=DEFAULT_REMINDER_MINUTES)
    args = parser.parse_args()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise SystemExit(
            "Completa TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env antes de usar este script "
            "(ver el docstring de este archivo para como crear el bot y obtener el chat_id)."
        )

    ok = run(
        host=args.host, port=args.port, timeout=args.timeout,
        state_path=Path(args.state_file), reminder_minutes=args.reminder_minutes,
        token=settings.telegram_bot_token, chat_id=settings.telegram_chat_id,
    )
    sys.exit(0 if ok else 1)
