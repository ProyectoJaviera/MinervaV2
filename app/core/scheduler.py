"""Loop asincrono del generador de senales y de la apertura automatica.

universo vigente + TODAS las estrategias (`app.trading.signal_generator`)
-> candidatos agrupados -> motor de riesgo en vivo (`PaperBackend.open_position`)
-> persistencia.

Este loop SOLO abre posiciones. Los cierres (SL, TP, trailing, liquidacion,
manual) los hace el monitor de posiciones (`app.trading.position_monitor`) con
el WebSocket; una senal contraria con posicion abierta se ignora, igual que el
backtest (docs/FASE2_CRITERIOS.md punto 6).

La apertura automatica esta desactivada por defecto (`AUTO_OPEN_WITHOUT_LLM=false`)
hasta la subfase 3.6: sin LLM las senales se registran en `signals` pero no
llegan a la cuenta real.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.core.logging import get_logger
from app.execution.paper_backend import InsufficientRiskBudgetError, PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.market.bitunix_ws import BitunixPublicWSClient
from app.persistence.database import Database
from app.persistence.repositories import trades_repo, universe_repo
from app.trading import risk_engine
from app.trading.signal_generator import (
    SignalCandidate,
    pick_representative_strategy,
    run_signal_generation_cycle,
)

logger = get_logger(__name__)


class Scheduler:
    def __init__(
        self,
        db: Database,
        rest_client: BitunixRestClient,
        paper_backend: PaperBackend,
        settings: Settings,
        ws_client: BitunixPublicWSClient | None = None,
    ) -> None:
        self.db = db
        self.rest_client = rest_client
        self.paper_backend = paper_backend
        self.settings = settings
        self.ws_client = ws_client
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_cycle()
            except Exception:  # noqa: BLE001 - un ciclo no debe tumbar el loop
                logger.exception("Error en el ciclo del generador de senales")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.scheduler_poll_seconds
                )
            except TimeoutError:
                pass

    async def run_cycle(self) -> list[SignalCandidate]:
        candidates = await run_signal_generation_cycle(self.db, self.rest_client, self.settings)
        for candidate in candidates:
            try:
                await self._process_candidate(candidate)
            except Exception:  # noqa: BLE001 - un candidato no debe tumbar el resto del ciclo
                logger.exception("Error procesando candidato de senal en %s", candidate.symbol)
        await self.sync_ws_subscriptions()
        return candidates

    async def sync_ws_subscriptions(self) -> None:
        if self.ws_client is None:
            return
        symbols = set(await universe_repo.get_included_symbols(self.db))
        symbols.update(p.symbol for p in await trades_repo.get_open_positions(self.db))
        await self.ws_client.set_symbols(sorted(symbols))

    async def _process_candidate(self, candidate: SignalCandidate) -> None:
        """Abre una posicion si el simbolo esta libre. Una senal contraria con
        posicion abierta se IGNORA (igual que el backtest): nunca cierra ni
        invierte. Un simbolo admite una sola posicion a la vez."""
        if not self.settings.auto_open_without_llm:
            return  # sin LLM (subfase 3.6) la apertura automatica esta desactivada

        if await trades_repo.get_open_positions(self.db, candidate.symbol):
            return

        representative = pick_representative_strategy(
            candidate.contributing_strategies, self.settings
        )
        try:
            await self.paper_backend.open_position(
                symbol=candidate.symbol,
                side=candidate.side,
                margin_usdt=self.settings.default_margin_usdt,
                leverage=self.settings.leverage,
                strategy=representative,
                sl_margin_loss_pct=candidate.sl_margin_loss_pct,
                levels=candidate.levels_by_strategy.get(representative),
            )
        except (risk_engine.RiskRejectedError, InsufficientRiskBudgetError) as exc:
            logger.info("No se abrio posicion en %s: %s", candidate.symbol, exc)
