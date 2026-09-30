"""Rate limiter simple tipo token-bucket para respetar el limite de Bitunix
(10 req/s por IP, verificado en docs/FASE0.md para todos los endpoints publicos
usados)."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, max_per_second: int) -> None:
        self.max_per_second = max_per_second
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            window_start = now - 1.0
            self._timestamps = [t for t in self._timestamps if t > window_start]
            if len(self._timestamps) >= self.max_per_second:
                sleep_for = self._timestamps[0] + 1.0 - now
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._timestamps.append(time.monotonic())
