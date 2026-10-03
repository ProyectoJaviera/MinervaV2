"""Loop asincrono que cierra el pipeline end-to-end:

universo vigente + TODAS las estrategias (`app.trading.signal_generator`)
-> candidatos de senal agrupados (sin duplicar por confluencia entre
estrategias) -> motor de riesgo en vivo (`app.trading.risk_engine`, via
`PaperBackend.open_position`) -> persistencia -> (consultable desde la
API/frontend).

**Reemplaza, Fase 3 subfase 3.3, el diseno de Fase 1** (`settings.
active_strategy` sobre `settings.symbols`, un solo simbolo/una sola
estrategia -- ambos settings quedan vestigiales, ver docs/FASE3_PLAN.md
punto 1): ahora itera el universo dinamico completo (`asset_universe`) y
las 6 estrategias del registro, cada una en sus timeframes.

Sin LLM, sin noticias, sin memoria -- eso llega en fases posteriores (ver
docs/FASE3_PLAN.md).
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.core.logging import get_logger
from app.execution.paper_backend import InsufficientRiskBudgetError, PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.repositories import trades_repo
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
    ) -> None:
        self.db = db
        self.rest_client = rest_client
        self.paper_backend = paper_backend
        self.settings = settings
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
        """Un poll completo: genera senales sobre todo el universo
        vigente, agrupa las accionables por confluencia y, para cada
        candidato, intenta abrir/revertir la posicion real (paper) --
        expone la lista de candidatos para que los tests puedan inspeccionar
        el resultado del generador sin depender de `trades`."""
        candidates = await run_signal_generation_cycle(self.db, self.rest_client, self.settings)
        for candidate in candidates:
            try:
                await self._process_candidate(candidate)
            except Exception:  # noqa: BLE001 - un candidato no debe tumbar el resto del ciclo
                logger.exception("Error procesando candidato de senal en %s", candidate.symbol)
        return candidates

    async def _process_candidate(self, candidate: SignalCandidate) -> None:
        """Abre una posicion si el simbolo esta libre. Una senal contraria con
        posicion abierta se IGNORA (igual que el backtest, ver
        docs/FASE2_CRITERIOS.md punto 6): nunca cierra ni invierte. Las
        posiciones se cierran solo por SL/TP/trailing/liquidacion/manual."""
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
            )
        except (risk_engine.RiskRejectedError, InsufficientRiskBudgetError) as exc:
            logger.info("No se abrio posicion en %s: %s", candidate.symbol, exc)
