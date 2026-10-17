"""Arranque AISLADO del bot de Telegram dentro del `lifespan` (subfase 3.7).

Garantias (requisito duro: el bot de Telegram nunca puede tumbar ni frenar el
scheduler, el etiquetado ni el monitor):
- Apagado por defecto: sin `TELEGRAM_COMMANDS_ENABLED=true`, token y chat_id, ni se
  construye (el dev server y los tests nunca consumen `getUpdates`: un token solo
  admite un consumidor de long-polling).
- Construirlo no puede lanzar: cualquier excepcion se registra (solo el tipo) y el
  bot queda desactivado; el arranque del resto sigue.
- Corre en su PROPIA tarea y `run_isolated` captura cualquier `Exception` que
  escape de `run_forever`, asi la tarea nunca termina con una excepcion que el
  cierre ordenado del `lifespan` tendria que propagar."""

from __future__ import annotations

import asyncio

import httpx

from app.config import Settings
from app.core.logging import get_logger
from app.telegram.bot import TelegramBot
from app.telegram.client import DEFAULT_BASE_URL, TelegramClient
from app.telegram.commands import BotContext, CommandRouter

logger = get_logger(__name__)


def build_telegram_bot(
    settings: Settings, ctx: BotContext, *, transport: httpx.AsyncBaseTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> TelegramBot | None:
    """El bot listo para correr, o `None` (desactivado) sin lanzar nunca."""
    try:
        if not settings.telegram_commands_enabled:
            logger.info("Bot de Telegram desactivado (TELEGRAM_COMMANDS_ENABLED no es true).")
            return None
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            logger.warning(
                "Bot de Telegram NO iniciado: TELEGRAM_COMMANDS_ENABLED=true pero falta "
                "TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."
            )
            return None
        if settings.telegram_chat_id.strip().startswith("-"):
            logger.warning(
                "TELEGRAM_CHAT_ID parece de un grupo o canal (negativo): el bot solo acepta "
                "chats PRIVADOS, asi que no respondera a nadie."
            )
        client = TelegramClient(settings.telegram_bot_token, transport=transport, base_url=base_url)
        return TelegramBot(
            client=client, allowed_chat_id=settings.telegram_chat_id, handler=CommandRouter(ctx),
        )
    except Exception as exc:  # noqa: BLE001 - construirlo jamas impide arrancar el sistema
        logger.error("Bot de Telegram NO iniciado (%s); el resto del sistema sigue.",
                     type(exc).__name__)
        return None


async def run_isolated(bot: TelegramBot) -> None:
    try:
        await bot.run_forever()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - un bug del bot no se propaga a nadie
        logger.error(
            "El bot de Telegram termino de forma inesperada (%s); el scheduler, el etiquetado "
            "y el monitor siguen funcionando.", type(exc).__name__,
        )


def maybe_start_telegram_bot(
    settings: Settings, ctx: BotContext, tasks: list[asyncio.Task],
) -> TelegramBot | None:
    """Si corresponde, crea la tarea del bot y la agrega a `tasks` (para que el
    cierre ordenado del `lifespan` la cancele igual que a las demas)."""
    bot = build_telegram_bot(settings, ctx)
    if bot is None:
        return None
    try:
        tasks.append(asyncio.create_task(run_isolated(bot)))
    except Exception as exc:  # noqa: BLE001
        logger.error("Bot de Telegram NO iniciado (%s); el resto del sistema sigue.",
                     type(exc).__name__)
        return None
    logger.info("Bot de Telegram iniciado (long-polling; solo responde al chat permitido).")
    return bot
