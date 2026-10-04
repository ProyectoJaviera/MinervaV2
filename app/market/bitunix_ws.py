"""Cliente WebSocket publico de Bitunix Futures.

Verificado en `www.bitunix.com/api-docs/futures/websocket/...` (ver
docs/FASE0.md):
- URL: wss://fapi.bitunix.com/public/
- Suscripcion: {"op": "subscribe", "args": [{"symbol": "BTCUSDT", "ch": "tickers"}]}
- Heartbeat: {"op": "ping", "ping": <unix_seconds>} -> servidor responde pong.
- Push del canal "tickers": {"ch": "tickers", "ts": ..., "data": [{"s": "BTCUSDT",
  "la": "<last price>", "o": .., "h": .., "l": .., "b": .., "q": .., ...}]}.

En la vertical minima de Fase 1 la ejecucion usa el endpoint REST de tickers
(que si expone markPrice explicito); este cliente WS queda operativo y testeado
de forma independiente para tiempo real, y se integra a la decision/ejecucion
en una fase posterior si se requiere evaluacion a nivel de tick (SPEC lo exige
para SL/TP/trailing en el simulador realista de Fase 3).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

import websockets
from websockets.exceptions import WebSocketException

from app.core.logging import get_logger

logger = get_logger(__name__)

PING_INTERVAL_SECONDS = 15
MAX_BACKOFF_SECONDS = 60


class BitunixPublicWSClient:
    def __init__(
        self,
        ws_url: str,
        symbols: list[str],
        channel: str = "tickers",
        on_message: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.symbols = list(dict.fromkeys(symbols))
        self.channel = channel
        self.on_message = on_message
        self._stop = asyncio.Event()
        self._ws = None
        self._subscribed: set[str] = set()

    def stop(self) -> None:
        self._stop.set()

    async def set_symbols(self, symbols: list[str]) -> None:
        """Cambia el conjunto de simbolos suscritos. Si hay conexion viva, suscribe
        en caliente solo los nuevos; si no, se suscribe al reconectar."""
        self.symbols = list(dict.fromkeys(symbols))
        new = [s for s in self.symbols if s not in self._subscribed]
        if self._ws is not None and new:
            await self._ws.send(json.dumps(self._subscribe_payload(new)))
            self._subscribed.update(new)

    def _subscribe_payload(self, symbols: list[str]) -> dict:
        return {"op": "subscribe", "args": [{"symbol": s, "ch": self.channel} for s in symbols]}

    async def run_forever(self) -> None:
        """Mantiene la conexion viva; reconecta con backoff exponencial ante
        cualquier corte, hasta que se llama a `stop()`."""
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._run_once()
                attempt = 0  # conexion exitosa: resetea el backoff
            except (TimeoutError, WebSocketException, OSError) as exc:
                if self._stop.is_set():
                    break
                backoff = min(2**attempt, MAX_BACKOFF_SECONDS)
                logger.warning(
                    "WS Bitunix desconectado (%s); reintentando en %ss", exc, backoff
                )
                attempt += 1
                await asyncio.sleep(backoff)

    async def _run_once(self) -> None:
        async with websockets.connect(self.ws_url, ping_interval=None) as ws:
            self._ws = ws
            self._subscribed = set()
            try:
                if self.symbols:
                    await ws.send(json.dumps(self._subscribe_payload(self.symbols)))
                    self._subscribed.update(self.symbols)
                heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                try:
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        await self._handle_message(raw)
                finally:
                    heartbeat_task.cancel()
            finally:
                self._ws = None
                self._subscribed = set()

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await ws.send(json.dumps({"op": "ping", "ping": int(time.time())}))

    async def _handle_message(self, raw: str | bytes) -> None:
        """Entrega TODO mensaje JSON del servidor (pong incluido): el monitor usa
        cualquier mensaje como latido del feed (subfase 3.4)."""
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Mensaje WS no-JSON ignorado: %r", raw)
            return
        if not isinstance(message, dict):
            return
        if self.on_message is not None:
            await self.on_message(message)
