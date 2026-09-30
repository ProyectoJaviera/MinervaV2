"""Punto de entrada FastAPI. Sin autenticacion todavia (se agrega en Fase 5
junto al resto del dashboard) -- ver docs/FASE0.md y el plan de Fase 1.

Indicador permanente de modo paper trading: ver `GET /health` y el header
`X-Minerva-Mode` en todas las respuestas.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, positions, trades
from app.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.warning("Minerva arrancando en MODO PAPER TRADING -- nunca se envian ordenes reales.")

    db = Database(settings.database_path)
    await db.connect()

    rest_client = BitunixRestClient(
        base_url=settings.bitunix_rest_base_url,
        rate_limit_per_sec=settings.bitunix_rate_limit_per_sec,
    )
    paper_backend = PaperBackend(db, rest_client, settings)
    scheduler = Scheduler(db, rest_client, paper_backend, settings)

    app.state.db = db
    app.state.rest_client = rest_client
    app.state.paper_backend = paper_backend
    app.state.scheduler = scheduler

    scheduler_task = asyncio.create_task(scheduler.run_forever())
    try:
        yield
    finally:
        scheduler.stop()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await rest_client.aclose()
        await db.close()


app = FastAPI(title="Minerva (PAPER TRADING)", lifespan=lifespan)


@app.middleware("http")
async def add_paper_trading_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Minerva-Mode"] = "PAPER_TRADING"
    return response


app.include_router(health.router)
app.include_router(positions.router)
app.include_router(trades.router)
