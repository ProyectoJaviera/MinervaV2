"""Confirmacion de dos pasos para los comandos que cambian el estado del bot
(`/pause`, `/resume`, `/kill`; subfase 3.7).

Primer mensaje (`/pause`): el bot advierte y registra una solicitud pendiente.
Segundo mensaje (`/pause SI`) dentro de `CONFIRM_WINDOW_SECONDS`: ejecuta. Reglas:
- Solo vale un `SI` precedido por una solicitud VIGENTE DEL MISMO comando.
- Solo hay UNA solicitud pendiente a la vez: pedir otro comando cancela la anterior
  (un `/pause SI` no puede ejecutarse por una solicitud vieja de `/pause` si en
  medio se pidio `/kill`).
- La solicitud se consume al confirmar (un `SI` repetido no vuelve a ejecutar).
- Vive en memoria: un reinicio la descarta, a proposito (un mensaje viejo
  reprocesado nunca ejecuta nada por si solo -- y el bot descarta lo pendiente
  al arrancar, ver `bot.py`).
- Reloj monotono inyectable para las pruebas."""

from __future__ import annotations

import time
from collections.abc import Callable

CONFIRM_WINDOW_SECONDS = 60.0
YES_WORDS = frozenset({"si", "sí"})


def is_yes(args: list[str]) -> bool:
    return len(args) == 1 and args[0].lower() in YES_WORDS


class ConfirmationManager:
    def __init__(
        self, *, window_seconds: float = CONFIRM_WINDOW_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_seconds = window_seconds
        self._now = now
        self._pending: tuple[str, float] | None = None  # (comando, vence en)

    def request(self, command: str) -> None:
        self._pending = (command, self._now() + self.window_seconds)

    def confirm(self, command: str) -> bool:
        """True (y consume la solicitud) solo si hay una vigente del MISMO comando."""
        pending = self._pending
        if pending is None:
            return False
        pending_command, expires_at = pending
        if self._now() > expires_at:
            self._pending = None
            return False
        if pending_command != command:
            return False
        self._pending = None
        return True
