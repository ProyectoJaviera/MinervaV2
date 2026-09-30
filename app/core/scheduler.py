"""Loop asincrono que cierra la vertical minima end-to-end:

datos reales de Bitunix -> senal de la estrategia por reglas -> operacion en
papel -> persistencia -> (consultable desde la API/frontend).

Sin LLM, sin noticias, sin memoria, sin gestion de riesgo completa (SL/TP/
trailing/liquidacion) -- eso llega en fases posteriores, ver docs/FASE0.md y
el plan de Fase 1.
"""

from __future__ import annotations

import asyncio
import time

import pandas as pd

from app.config import Settings
from app.core.logging import get_logger
from app.execution.paper_backend import InsufficientRiskBudgetError, PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.models import Side
from app.persistence.repositories import trades_repo
from app.strategies.base import Signal
from app.strategies.registry import get_strategy

logger = get_logger(__name__)


class Scheduler:
    def __init__(
        self,
        db: Database,
        rest_client: BitunixRestClient,
        paper_backend: PaperBackend,
        settings: Settings,
    ) -> None:
        self.db = db
        self.rest_client = rest_client
        self.paper_backend = paper_backend
        self.settings = settings
        self.strategy = get_strategy(settings.active_strategy)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            for symbol in self.settings.symbols:
                try:
                    await self._evaluate_symbol(symbol)
                except Exception:  # noqa: BLE001 - un simbolo no debe tumbar el loop
                    logger.exception("Error evaluando %s", symbol)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.scheduler_poll_seconds
                )
            except TimeoutError:
                pass

    async def _evaluate_symbol(self, symbol: str) -> None:
        end_time = int(time.time() * 1000)
        raw_bars = await self.rest_client.get_kline(
            symbol=symbol,
            interval=self.settings.kline_interval,
            end_time=end_time,
            limit=min(self.settings.strategy_history_bars, 200),
            price_type="LAST_PRICE",
        )
        if len(raw_bars) < 2:
            logger.debug("Sin suficientes velas para %s todavia", symbol)
            return

        df = pd.DataFrame(raw_bars).astype(
            {"open": float, "high": float, "low": float, "close": float}
        ).sort_values("time").reset_index(drop=True)

        signal = self.strategy.evaluate(df)
        open_positions = await trades_repo.get_open_positions(self.db, symbol)

        if signal == Signal.HOLD:
            return

        desired_side = Side.LONG if signal == Signal.LONG else Side.SHORT
        opposite_open = [p for p in open_positions if p.side != desired_side]
        for pos in opposite_open:
            assert pos.id is not None
            await self.paper_backend.close_position(pos.id, reason="SIGNAL_REVERSAL")

        if opposite_open:
            open_positions = await trades_repo.get_open_positions(self.db, symbol)
        if any(p.side == desired_side for p in open_positions):
            return  # ya hay una posicion abierta en la direccion deseada

        try:
            await self.paper_backend.open_position(
                symbol=symbol,
                side=desired_side,
                margin_usdt=self.settings.default_margin_usdt,
                leverage=self.settings.leverage,
                strategy=self.strategy.name,
            )
        except InsufficientRiskBudgetError as exc:
            logger.info("No se abrio posicion en %s: %s", symbol, exc)
