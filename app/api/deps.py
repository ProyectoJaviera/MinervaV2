from __future__ import annotations

from fastapi import Request

from app.core.heartbeat import Heartbeat
from app.core.scheduler import Scheduler
from app.persistence.database import Database
from app.trading.shadow_live_labeler import ShadowLiveLabeler


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_heartbeat(request: Request) -> Heartbeat:
    return request.app.state.heartbeat


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


def get_live_labeler(request: Request) -> ShadowLiveLabeler:
    return request.app.state.live_labeler
