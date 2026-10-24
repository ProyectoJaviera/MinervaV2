"""Bucle asincrono del scheduler: genera senales, abre sombras y dispara el etiquetado.

Cadena de cada ciclo: universo vigente y TODAS las estrategias
(`run_signal_generation_cycle`) -> candidatos agrupados -> sombra (`ShadowBook`) ->
etiquetado en vivo (`ShadowLiveLabeler`, en segundo plano) y, solo con la apertura
automatica sin LLM (`AUTO_OPEN_WITHOUT_LLM`, apagada), apertura en la cuenta paper.
Este bucle SOLO abre: los cierres (SL, TP, trailing, liquidacion, manual) los hace el
monitor de posiciones. Las aperturas con LLM no pasan por aqui: las hace el brazo
ejecutado (`app.trading.paper_execution`) cuando el etiquetado guarda una APROBADA.
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
    """Orquesta un ciclo de senales cada `SCHEDULER_POLL_SECONDS` hasta que se pide parar.

    Un error en un ciclo, o en una sombra o candidato concreto, se registra y no tumba el
    bucle. Pausar el scheduler (`control_state`) detiene la medicion: no hay senales,
    sombras ni etiquetado mientras dure.

    Attributes:
        last_cycle_at: fin del ultimo ciclo COMPLETO (no el latido); `GET /health` lo
            expone y pasa a `stale` si se queda atras.
    """
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
        """Pide que `run_forever` termine despues de su espera actual."""
        self._stop.set()

    async def run_forever(self) -> None:
        """Ejecuta `run_cycle` y espera `SCHEDULER_POLL_SECONDS`, en bucle, hasta `stop()`.

        Efectos:
            Ver `run_cycle`. Una excepcion del ciclo se registra y el bucle sigue.
        """
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
        """Un ciclo completo: senales, sombras, etiquetado en vivo y suscripciones del feed.

        Con el scheduler pausado devuelve una lista vacia SIN avanzar `last_cycle_at`, de modo
        que `/health` pasa a `stale` y la alerta externa avisa (una pausa que detiene la
        medicion no puede ser silenciosa). Una vez por ciclo avisa al etiquetador
        (`note_cycle_start`, que comprueba el hash congelado). Por cada candidato abre la sombra,
        dispara `maybe_label` (fuego y olvido, con el candidato para el brazo ejecutado) y, solo
        si `AUTO_OPEN_WITHOUT_LLM` esta activo, intenta abrir en la cuenta paper y marca la
        sombra como ejecutada.

        Returns:
            Los candidatos agrupados del ciclo (vacia si estaba pausado).

        Efectos:
            Lee `system_state` (pausa). Red: Bitunix (velas, funding, contratos) y la
            suscripcion del WebSocket. Escribe `signals`, `signals_discarded_by_sl_cap`,
            `shadow_trades`, caches de mercado y, con la apertura sin LLM, `trades`. El
            etiquetado escribe `llm_logs` en su propia tarea.
        """
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
                    # Fuego y olvido: no bloquea el resto del ciclo (TAREA B). El candidato
                    # solo viaja hasta el gancho del brazo ejecutado (subfase 3.9).
                    self.live_labeler.maybe_label(shadow_trade, candidate)
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
        """Suscribe el WebSocket al universo vigente mas los simbolos con posicion abierta.

        Efectos:
            Lee `asset_universe` y `trades`. Red: mensaje `subscribe` al WebSocket de Bitunix.
        """
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
