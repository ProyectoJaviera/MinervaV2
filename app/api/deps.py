"""Dependencias de FastAPI: acceso a los objetos que `lifespan` cuelga en `app.state`.

Las que devuelven `None` cuando falta el objeto (`get_paper_arm`, `get_news_collector`,
`get_push_notifier`, `get_scheduler_or_none`, `get_paper_backend`, `get_monitor`) existen
para que una app minima (pruebas) no falle.
"""

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
    """La base de datos del proceso (`app.state.db`).

    Args:
        request: la peticion.

    Returns:
        La `Database`.
    """
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    """La configuracion del proceso (`app.state.settings`).

    Args:
        request: la peticion.

    Returns:
        Los `Settings`.
    """
    return request.app.state.settings


def get_heartbeat(request: Request) -> Heartbeat:
    """El latido del proceso (`app.state.heartbeat`).

    Args:
        request: la peticion.

    Returns:
        El `Heartbeat`.
    """
    return request.app.state.heartbeat


def get_scheduler(request: Request) -> Scheduler:
    """El scheduler (`app.state.scheduler`).

    Args:
        request: la peticion.

    Returns:
        El `Scheduler`.
    """
    return request.app.state.scheduler


def get_live_labeler(request: Request) -> ShadowLiveLabeler:
    """El etiquetador en vivo (`app.state.live_labeler`).

    Args:
        request: la peticion.

    Returns:
        El `ShadowLiveLabeler`.
    """
    return request.app.state.live_labeler


def get_news_collector(request: Request) -> NewsCollector | None:
    """El colector de noticias (Fase 4).

    Args:
        request: la peticion.

    Returns:
        El colector, o `None` si esta apagado o no se construyo.
    """
    return getattr(request.app.state, "news_collector", None)


def get_push_notifier(request: Request) -> PushNotifier | None:
    """El notificador push de Telegram (Fase 5).

    Args:
        request: la peticion.

    Returns:
        El notificador, o `None` si esta apagado o no se construyo.
    """
    return getattr(request.app.state, "push_notifier", None)


def get_paper_arm(request: Request) -> PaperExecutionArm | None:
    """El brazo ejecutado de la cuenta paper (subfase 3.9).

    Args:
        request: la peticion.

    Returns:
        El brazo, o `None` si el proceso no lo creo.
    """
    return getattr(request.app.state, "paper_execution", None)


def get_scheduler_or_none(request: Request) -> Scheduler | None:
    """El scheduler si el proceso lo tiene.

    Args:
        request: la peticion.

    Returns:
        El `Scheduler`, o `None` en una app minima (pruebas).
    """
    return getattr(request.app.state, "scheduler", None)


def get_paper_backend(request: Request):
    """El `PaperBackend` de la cuenta paper.

    Args:
        request: la peticion.

    Returns:
        El backend, o `None` en una app sin el (p. ej. pruebas).
    """
    return getattr(request.app.state, "paper_backend", None)


def get_monitor(request: Request):
    """El `PositionMonitor`; sus `marks` son los precios en vivo.

    Args:
        request: la peticion.

    Returns:
        El monitor, o `None` si no esta.
    """
    return getattr(request.app.state, "monitor", None)
