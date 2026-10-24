"""Limitador de tasa para respetar el limite de Bitunix (10 pedidos por segundo por IP).

Verificado en `docs/historial/FASE0.md` para todos los endpoints publicos usados. Es de
ventana deslizante de un segundo, no un token bucket estricto.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Limitador de tasa de ventana deslizante de un segundo, para respetar el limite de Bitunix.

    Guarda las marcas de tiempo de los ultimos 1000 ms; si ya hay `max_per_second`, espera a
    que la mas vieja salga de la ventana. Es seguro entre corrutinas (candado interno).

    Attributes:
        max_per_second: llamadas permitidas por segundo.
    """
    def __init__(self, max_per_second: int) -> None:
        self.max_per_second = max_per_second
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Espera, si hace falta, hasta que se pueda hacer una llamada, y la registra."""
        async with self._lock:
            now = time.monotonic()
            window_start = now - 1.0
            self._timestamps = [t for t in self._timestamps if t > window_start]
            if len(self._timestamps) >= self.max_per_second:
                sleep_for = self._timestamps[0] + 1.0 - now
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._timestamps.append(time.monotonic())
