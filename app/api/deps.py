from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.core.heartbeat import Heartbeat
from app.core.scheduler import Scheduler
from app.persistence.database import Database
from app.trading.paper_execution import PaperExecutionArm
from app.trading.shadow_live_labeler import ShadowLiveLabeler


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_heartbeat(request: Request) -> Heartbeat:
    return request.app.state.heartbeat


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


def get_live_labeler(request: Request) -> ShadowLiveLabeler:
    return request.app.state.live_labeler


def get_paper_arm(request: Request) -> PaperExecutionArm | None:
    """El brazo ejecutado de la cuenta paper (subfase 3.9); `None` si el proceso no lo creo."""
    return getattr(request.app.state, "paper_execution", None)
