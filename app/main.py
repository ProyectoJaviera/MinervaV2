"""Punto de entrada FastAPI. `GET /health`/`/positions`/`/trades` siguen sin
auth (preexistentes, nada sensible); las rutas nuevas bajo `/api/` (pre-Fase
5, item 2 del plan aprobado 2026-10-14) exigen login -- ver
`app/api/security.py`. El dashboard completo sigue siendo Fase 5 -- ver
docs/FASE0.md y el plan de Fase 1.

Indicador permanente de modo paper trading: ver `GET /health` y el header
`X-Minerva-Mode` en todas las respuestas.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    auth,
    health,
    llm_decisions,
    measurement,
    positions,
    shadow_trades,
    trades,
)
from app.api.security import warn_if_jwt_secret_is_unsafe
from app.config import settings
from app.core.heartbeat import Heartbeat
from app.core.logging import get_logger, setup_logging, shutdown_logging
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.market.bitunix_ws import BitunixPublicWSClient
from app.market.coingecko_client import CoinGeckoClient
from app.market.universe import UniverseRefresher
from app.persistence.database import Database
from app.trading.position_monitor import PositionMonitor
from app.trading.shadow_book import ShadowBook
from app.trading.shadow_live_labeler import ShadowLiveLabeler, build_decision_service

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.warning("Minerva arrancando en MODO PAPER TRADING -- nunca se envian ordenes reales.")
    warn_if_jwt_secret_is_unsafe(logger, settings)

    db = Database(settings.database_path)
    await db.connect()

    rest_client = BitunixRestClient(
        base_url=settings.bitunix_rest_base_url,
        rate_limit_per_sec=settings.bitunix_rate_limit_per_sec,
    )
    paper_backend = PaperBackend(db, rest_client, settings)
    shadow_book = ShadowBook(db, settings)
    monitor = PositionMonitor(db, rest_client, paper_backend, settings, shadow=shadow_book)
    ws_client = BitunixPublicWSClient(
        ws_url=settings.bitunix_ws_public_url, symbols=[], on_message=monitor.handle_ws_message,
    )
    # TAREA B (docs/FASE3_6_LLM.md seccion k): apagado por defecto
    # (`settings.llm_live_labeling_enabled`) -- `ShadowLiveLabeler` en si
    # decide si corre o no en cada ciclo, construirlo aqui no activa nada.
    live_labeler = ShadowLiveLabeler(db, settings, build_decision_service(db, settings))
    scheduler = Scheduler(
        db, rest_client, paper_backend, settings, ws_client=ws_client, shadow=shadow_book,
        live_labeler=live_labeler,
    )
    coingecko_client = CoinGeckoClient(
        base_url=settings.coingecko_base_url, api_key=settings.coingecko_api_key,
    )
    universe_refresher = UniverseRefresher(coingecko_client, rest_client, db, settings)
    heartbeat = Heartbeat(settings.heartbeat_seconds)

    # Reconciliacion ANTES de abrir el feed y el generador: el periodo caido se
    # reproduce con velas 1m antes de que entren ticks nuevos.
    try:
        outcomes = await monitor.reconcile_on_startup()
        logger.info("Reconciliacion al arrancar: %s", outcomes)
    except Exception:  # noqa: BLE001 - un fallo aqui no debe impedir arrancar
        logger.critical("Fallo la reconciliacion al arrancar", exc_info=True)

    await monitor.start_feed()
    await scheduler.sync_ws_subscriptions()

    app.state.db = db
    app.state.rest_client = rest_client
    app.state.paper_backend = paper_backend
    app.state.monitor = monitor
    app.state.scheduler = scheduler
    app.state.heartbeat = heartbeat
    app.state.live_labeler = live_labeler
    app.state.settings = settings

    tasks = [
        asyncio.create_task(ws_client.run_forever()),
        asyncio.create_task(monitor.run_forever()),
        asyncio.create_task(scheduler.run_forever()),
        asyncio.create_task(universe_refresher.run_forever()),
        asyncio.create_task(heartbeat.run_forever()),
    ]
    try:
        yield
    finally:
        scheduler.stop()
        monitor.stop()
        ws_client.stop()
        universe_refresher.stop()
        heartbeat.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await live_labeler.shutdown()
        await monitor.shutdown()
        await coingecko_client.aclose()
        await rest_client.aclose()
        await db.close()
        shutdown_logging()


app = FastAPI(title="Minerva (PAPER TRADING)", lifespan=lifespan)


@app.middleware("http")
async def add_paper_trading_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Minerva-Mode"] = "PAPER_TRADING"
    return response


app.include_router(health.router)
app.include_router(positions.router)
app.include_router(trades.router)
app.include_router(auth.router)
app.include_router(measurement.router)
app.include_router(llm_decisions.router)
app.include_router(shadow_trades.router)

# Subfase D (plan pre-Fase 5 aprobado 2026-10-14): el build estatico del
# dashboard (`npm --prefix frontend run build`), si existe. Montado AL FINAL
# -- Starlette resuelve rutas en el orden en que se agregan, asi que todo lo
# de arriba (`/health`, `/api/*`) siempre gana antes de caer en este
# catch-all. Condicional a proposito: un checkout que nunca corrio el build
# no debe fallar al arrancar. `html=True` sirve `index.html` en `/` (unica
# ruta de cliente real, sin router todavia).
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
