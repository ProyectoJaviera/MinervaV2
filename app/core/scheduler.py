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
from datetime import UTC, datetime

from app.config import Settings
from app.core.logging import get_logger
from app.execution.paper_backend import InsufficientRiskBudgetError, PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.market.bitunix_ws import BitunixPublicWSClient
from app.persistence.database import Database
from app.persistence.repositories import shadow_repo, trades_repo, universe_repo
from app.trading import control_state, risk_engine
from app.trading.shadow_book import ShadowBook
from app.trading.shadow_live_labeler import ShadowLiveLabeler
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
        shadow: ShadowBook | None = None,
        live_labeler: ShadowLiveLabeler | None = None,
    ) -> None:
        self.db = db
        self.shadow = shadow
        self.rest_client = rest_client
        self.paper_backend = paper_backend
        self.settings = settings
        self.ws_client = ws_client
        self.live_labeler = live_labeler
        self._stop = asyncio.Event()
        # Incidente de estabilidad 2026-10-11: ultima evaluacion COMPLETA del
        # generador de senales (no el heartbeat, que solo prueba que el event
        # loop sigue vivo) -- expuesto en GET /health como `last_cycle_at`.
        self.last_cycle_at: datetime | None = None
        self._pause_logged = False

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
        # Pausa pedida por Telegram (subfase 3.7, `app.trading.control_state`):
        # sin senales, sombras ni etiquetado = SIN medicion. `last_cycle_at` NO
        # avanza, asi `/health` pasa a "stale" y la alerta externa avisa: una
        # pausa que detiene la medicion no puede ser silenciosa. Los cierres de
        # posiciones (PositionMonitor) no pasan por aqui.
        if await control_state.is_scheduler_paused(self.db):
            if not self._pause_logged:
                logger.warning(
                    "Scheduler PAUSADO (system_state.scheduler_paused): no se generan senales, "
                    "sombras ni etiquetado -- la medicion esta detenida. Reanudar con /resume."
                )
                self._pause_logged = True
            return []
        self._pause_logged = False
        if self.live_labeler is not None:
            # Una vez por ciclo (TAREA B, docs/FASE3_6_LLM.md seccion k), no
            # una vez por candidato: deja en cache si el hash coincide con el
            # congelado para TODO este ciclo, con un WARNING si no.
            self.live_labeler.note_cycle_start()
        candidates = await run_signal_generation_cycle(self.db, self.rest_client, self.settings)
        for candidate in candidates:
            shadow_trade = None
            if self.shadow is not None:
                try:
                    shadow_trade = await self.shadow.open_candidate(candidate)
                except Exception:  # noqa: BLE001 - la sombra no debe tumbar el ciclo
                    logger.exception("Error abriendo operacion sombra en %s", candidate.symbol)
                if shadow_trade is not None and self.live_labeler is not None:
                    # Fuego y olvido: no bloquea el resto del ciclo (TAREA B).
                    self.live_labeler.maybe_label(shadow_trade)
            try:
                real = await self._process_candidate(candidate)
            except Exception:  # noqa: BLE001 - un candidato no debe tumbar el resto del ciclo
                logger.exception("Error procesando candidato de senal en %s", candidate.symbol)
                real = None
            if shadow_trade is not None and real is not None:
                await shadow_repo.mark_executed(self.db, shadow_trade.id)
        await self.sync_ws_subscriptions()
        self.last_cycle_at = datetime.now(UTC)
        return candidates

    async def sync_ws_subscriptions(self) -> None:
        if self.ws_client is None:
            return
        symbols = set(await universe_repo.get_included_symbols(self.db))
        symbols.update(p.symbol for p in await trades_repo.get_open_positions(self.db))
        await self.ws_client.set_symbols(sorted(symbols))

    async def _process_candidate(self, candidate: SignalCandidate):
        """Abre una posicion si el simbolo esta libre. Una senal contraria con
        posicion abierta se IGNORA (igual que el backtest): nunca cierra ni
        invierte. Un simbolo admite una sola posicion a la vez."""
        if not self.settings.auto_open_without_llm:
            return None  # sin LLM (subfase 3.6) la apertura automatica esta desactivada

        if await trades_repo.get_open_positions(self.db, candidate.symbol):
            return None

        representative = pick_representative_strategy(
            candidate.contributing_strategies, self.settings
        )
        try:
            return await self.paper_backend.open_position(
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
            return None
