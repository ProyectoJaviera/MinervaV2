from __future__ import annotations

from fastapi import Request

from app.core.heartbeat import Heartbeat
from app.core.scheduler import Scheduler
from app.persistence.database import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_heartbeat(request: Request) -> Heartbeat:
    return request.app.state.heartbeat


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler
