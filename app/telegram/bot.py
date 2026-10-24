"""Bucle de long-polling del bot de Telegram (subfase 3.7).

**Allowlist estricta**: un update solo se procesa si viene de un chat PRIVADO cuyo
`id` coincide exactamente con `TELEGRAM_CHAT_ID`. Cualquier otro chat, usuario o
tipo de chat (un grupo con el mismo id configurado por error incluido) se IGNORA EN
SILENCIO: ni respuesta ni mensaje de error, para no confirmarle a un tercero que el
bot existe y escucha.

**Aislamiento (requisito duro)**: este bucle corre como una tarea aparte y nunca
puede tumbar ni frenar el scheduler, el etiquetado ni el monitor de posiciones.
- Toda excepcion de una iteracion (`Exception`, nunca `BaseException`: la
  cancelacion del cierre ordenado sigue funcionando) se captura, se registra SOLO
  por tipo (nunca la URL ni el token) y se reintenta con backoff exponencial con tope.
- Un WARNING por racha de fallos, no uno por intento.
- Una excepcion en el manejador de un comando se responde con "error interno" (solo
  al chat permitido) y el bucle sigue; el offset ya avanzo, asi que ese update no se
  repite en bucle.
- Tiempos de espera explicitos en el cliente: nada puede colgarse indefinidamente.

**Comandos viejos**: al arrancar descarta lo pendiente (no ejecuta nada acumulado
durante una caida) y descarta mensajes con mas de `max_message_age_seconds`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.telegram.client import TelegramClient

logger = get_logger(__name__)

POLL_TIMEOUT_SECONDS = 30
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 300.0
MAX_MESSAGE_AGE_SECONDS = 120.0
WARN_EVERY_N_FAILURES = 20
INTERNAL_ERROR_TEXT = "MODO PAPER TRADING\nError interno al procesar el comando. Revisa los logs."

# (texto del mensaje) -> texto de la respuesta, o None si no hay nada que responder.
CommandHandler = Callable[[str], Awaitable[str | None]]


def is_allowed_update(update: dict, allowed_chat_id: str | int) -> bool:
    """True solo para un mensaje de texto de un chat PRIVADO con el id exacto permitido.

    Cualquier otro chat, usuario o tipo de chat (un grupo con el mismo id incluido) se
    ignora en silencio, para no confirmarle a un tercero que el bot existe.

    Args:
        update: el update de la Bot API.
        allowed_chat_id: `TELEGRAM_CHAT_ID`.

    Returns:
        True si hay que procesarlo.
    """
    message = update.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        return False
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return False
    return str(chat.get("id")) == str(allowed_chat_id)


class TelegramBot:
    """Bucle de long-polling del bot de Telegram, aislado y con lista de permitidos estricta.

    Corre como una tarea aparte y nunca puede tumbar ni frenar el scheduler, el etiquetado
    ni el monitor: toda excepcion de una iteracion se captura, se registra SOLO por tipo
    (jamas la URL ni el token) y se reintenta con backoff exponencial con tope. Al arrancar
    descarta lo pendiente (no ejecuta nada acumulado durante una caida) y descarta mensajes
    de mas de `max_message_age_seconds`. El offset avanza ANTES de procesar: un update roto
    no se repite en bucle.

    Attributes:
        allowed_chat_id: el unico chat al que se atiende.
        ignored_count: updates ignorados (solo un contador; nunca se registra su contenido).
    """
    def __init__(
        self,
        *,
        client: TelegramClient,
        allowed_chat_id: str | int,
        handler: CommandHandler,
        poll_timeout: int = POLL_TIMEOUT_SECONDS,
        max_message_age_seconds: float = MAX_MESSAGE_AGE_SECONDS,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.allowed_chat_id = allowed_chat_id
        self.handler = handler
        self.poll_timeout = poll_timeout
        self.max_message_age_seconds = max_message_age_seconds
        self._now = now
        self._sleep = sleep
        self._offset: int | None = None
        self._drained = False
        self._failures = 0
        self._stop = asyncio.Event()
        self.ignored_count = 0  # solo un contador: nunca se registra el contenido

    def stop(self) -> None:
        """Pide que `run_forever` termine despues de su espera actual."""
        self._stop.set()

    async def process_update(self, update: dict) -> None:
        """Procesa un update: filtra, responde y nunca propaga una excepcion del comando.

        Un comando que falla se responde con "error interno" (solo al chat permitido) y el
        bucle sigue.

        Args:
            update: el update de la Bot API.

        Efectos:
            Red: `sendMessage` a Telegram. Lo que haga el manejador del comando (lecturas de la
            base; `/pause`, `/resume` y `/kill` escriben `system_state` y `control_audit`).
        """
        if not is_allowed_update(update, self.allowed_chat_id):
            self.ignored_count += 1
            return
        message = update["message"]
        date = message.get("date")
        if isinstance(date, (int, float)) and self._now() - date > self.max_message_age_seconds:
            self.ignored_count += 1
            return
        chat_id = message["chat"]["id"]
        try:
            reply = await self.handler(message["text"])
        except Exception as exc:  # noqa: BLE001 - un comando roto no tumba el bucle
            logger.error("Error procesando un comando de Telegram (%s)", type(exc).__name__)
            reply = INTERNAL_ERROR_TEXT
        if reply:
            await self._reply(chat_id, reply)

    async def _reply(self, chat_id: int | str, text: str) -> None:
        try:
            await self.client.send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001 - no poder responder no frena nada
            logger.warning("No se pudo responder por Telegram (%s)", type(exc).__name__)

    async def _poll_once(self) -> None:
        if not self._drained:
            # Descarta lo acumulado antes de arrancar (comandos viejos).
            updates = await self.client.get_updates(offset=self._offset, timeout=0)
            if updates:
                self._offset = max(u["update_id"] for u in updates) + 1
                return
            self._drained = True
            return
        updates = await self.client.get_updates(
            offset=self._offset, timeout=self.poll_timeout
        )
        for update in updates:
            self._offset = update["update_id"] + 1  # antes de procesar: nunca se repite
            await self.process_update(update)

    def _backoff_seconds(self) -> float:
        # Exponente acotado: con miles de fallos seguidos 2**n desbordaria el float.
        exponent = min(self._failures - 1, 20)
        return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2**exponent)

    async def run_forever(self) -> None:
        """Bucle de long-polling hasta `stop()`, con backoff exponencial ante fallos.

        Registra un WARNING por racha de fallos (no uno por intento) y cierra el cliente al salir.

        Efectos:
            Red: `getUpdates` y `sendMessage` a la Bot API de Telegram.
        """
        try:
            while not self._stop.is_set():
                try:
                    await self._poll_once()
                except Exception as exc:  # noqa: BLE001 - nada de aqui sale de esta tarea
                    self._failures += 1
                    if self._failures == 1 or self._failures % WARN_EVERY_N_FAILURES == 0:
                        logger.warning(
                            "Telegram: fallo en el long-polling (%s, %d consecutivos); "
                            "reintento con backoff. El scheduler y el etiquetado no se afectan.",
                            _describe(exc), self._failures,
                        )
                    await self._sleep(self._backoff_seconds())
                else:
                    if self._failures:
                        logger.info("Telegram: long-polling recuperado.")
                    self._failures = 0
        finally:
            try:
                await self.client.aclose()
            except Exception as exc:  # noqa: BLE001 - el cierre del cliente no propaga nada
                logger.warning("Telegram: error cerrando el cliente (%s)", type(exc).__name__)


def _describe(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}" + (f" HTTP {status}" if status else "")
