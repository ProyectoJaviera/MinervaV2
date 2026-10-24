"""Arranque AISLADO de las notificaciones push de Telegram dentro del `lifespan` (Fase 5), con
las mismas garantias que el bot de comandos (`app/telegram/runtime.py`), el colector de noticias
y el brazo ejecutado:

- Apagado por defecto: sin `TELEGRAM_PUSH_ENABLED=true`, token y chat, ni se construye (el dev
  server y los tests nunca escriben al chat). `start_bot.ps1` lo activa solo para el puerto 8000.
- Construirlo no puede lanzar: cualquier excepcion se registra (solo el tipo, nunca el token) y
  las notificaciones quedan desactivadas; el arranque del resto sigue.
- Corre en su PROPIA tarea y `run_isolated` captura cualquier `Exception` que escape: el
  scheduler, el etiquetado, el brazo ejecutado, el monitor y el bot de comandos no se enteran de
  nada de lo que le pase.
- No usa la API de Anthropic ni cuesta nada."""

from __future__ import annotations

import asyncio

import httpx

from app.config import Settings
from app.core.logging import get_logger
from app.persistence.database import Database
from app.telegram.client import DEFAULT_BASE_URL, TelegramClient
from app.telegram.push import PushNotifier

logger = get_logger(__name__)


def build_push_notifier(
    settings: Settings, db: Database, *, transport: httpx.AsyncBaseTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> PushNotifier | None:
    """El notificador listo para correr, o `None` (desactivado), sin lanzar nunca.

    Args:
        settings: configuracion (`TELEGRAM_PUSH_ENABLED`, token y chat).
        db: conexion a la base.
        transport: transporte HTTP de prueba (sin red), o `None`.
        base_url: URL base de la Bot API.

    Returns:
        El `PushNotifier`, o `None`.
    """
    try:
        if not settings.telegram_push_enabled:
            logger.info("Notificaciones push de Telegram desactivadas (TELEGRAM_PUSH_ENABLED no "
                        "es true).")
            return None
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            logger.warning(
                "Notificaciones push de Telegram NO iniciadas: TELEGRAM_PUSH_ENABLED=true pero "
                "falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."
            )
            return None
        client = TelegramClient(settings.telegram_bot_token, transport=transport, base_url=base_url)
        return PushNotifier(db, settings, client)
    except Exception as exc:  # noqa: BLE001 - construirlo jamas impide arrancar el sistema
        logger.error("Notificaciones push de Telegram NO iniciadas (%s); el resto del sistema "
                     "sigue.", type(exc).__name__)
        return None


async def run_isolated(notifier: PushNotifier) -> None:
    """Corre el notificador capturando cualquier `Exception` que escape.

    Args:
        notifier: el notificador.
    """
    try:
        await notifier.run_forever()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - un bug del notificador no se propaga a nadie
        logger.error(
            "Las notificaciones push terminaron de forma inesperada (%s); el scheduler, el "
            "etiquetado, el brazo ejecutado y el monitor siguen funcionando.", type(exc).__name__,
        )


def maybe_start_push_notifier(
    settings: Settings, db: Database, tasks: list[asyncio.Task],
) -> PushNotifier | None:
    """Si corresponde, crea la tarea del notificador y la agrega a `tasks`.

    Asi el cierre ordenado del `lifespan` la cancela igual que a las demas. Nunca lanza.

    Args:
        settings: configuracion.
        db: conexion a la base.
        tasks: lista de tareas del `lifespan`.

    Returns:
        El notificador, o `None` si esta desactivado.

    Efectos:
        Crea una tarea asyncio y agrega una entrada al log.
    """
    notifier = build_push_notifier(settings, db)
    if notifier is None:
        return None
    try:
        tasks.append(asyncio.create_task(run_isolated(notifier)))
    except Exception as exc:  # noqa: BLE001
        logger.error("Notificaciones push de Telegram NO iniciadas (%s); el resto del sistema "
                     "sigue.", type(exc).__name__)
        return None
    logger.info("Notificaciones push de Telegram iniciadas (tarea aislada, solo lee la base).")
    return notifier
