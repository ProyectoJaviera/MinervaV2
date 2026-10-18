from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.core.heartbeat import Heartbeat
from app.core.scheduler import Scheduler
from app.news.collector import NewsCollector
from app.persistence.database import Database
from app.telegram.push import PushNotifier
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


def get_news_collector(request: Request) -> NewsCollector | None:
    """El colector de noticias (Fase 4); `None` si esta apagado o no se construyo."""
    return getattr(request.app.state, "news_collector", None)


def get_push_notifier(request: Request) -> PushNotifier | None:
    """El notificador push de Telegram (Fase 5); `None` si esta apagado o no se construyo."""
    return getattr(request.app.state, "push_notifier", None)


def get_paper_arm(request: Request) -> PaperExecutionArm | None:
    """El brazo ejecutado de la cuenta paper (subfase 3.9); `None` si el proceso no lo creo."""
    return getattr(request.app.state, "paper_execution", None)


def get_scheduler_or_none(request: Request) -> Scheduler | None:
    """El scheduler si el proceso lo tiene; `None` en una app minima (pruebas)."""
    return getattr(request.app.state, "scheduler", None)


def get_paper_backend(request: Request):
    """El `PaperBackend` de la cuenta paper; `None` en una app sin el (p.ej. pruebas)."""
    return getattr(request.app.state, "paper_backend", None)


def get_monitor(request: Request):
    """El `PositionMonitor` (sus `marks` son los precios en vivo); `None` si no esta."""
    return getattr(request.app.state, "monitor", None)
