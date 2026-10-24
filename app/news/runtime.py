"""Arranque AISLADO del colector de noticias dentro del `lifespan` (Fase 4), con las mismas
garantias que el bot de Telegram (`app/telegram/runtime.py`):

- Apagado por defecto: sin `NEWS_COLLECTION_ENABLED=true` ni se construye (ni el dev server ni los
  tests tocan la red).
- Construirlo no puede lanzar: cualquier excepcion se registra (solo el tipo) y queda desactivado.
- Corre en su PROPIA tarea y `run_isolated` captura cualquier `Exception` que escape: el scheduler,
  el etiquetado, el brazo ejecutado y el monitor no se enteran de nada de lo que le pase.
- Sin costo: no usa la API de Anthropic."""

from __future__ import annotations

import asyncio

import httpx

from app.config import Settings
from app.core.logging import get_logger
from app.news.collector import NewsCollector
from app.news.fetch import HttpFetcher
from app.persistence.database import Database

logger = get_logger(__name__)


def build_news_collector(
    settings: Settings, db: Database, *, transport: httpx.AsyncBaseTransport | None = None,
) -> NewsCollector | None:
    """El colector listo para correr, o `None` (desactivado), sin lanzar nunca.

    Args:
        settings: configuracion (`NEWS_COLLECTION_ENABLED`, User-Agent y tope de respuesta).
        db: conexion a la base.
        transport: transporte HTTP de prueba (sin red), o `None`.

    Returns:
        El `NewsCollector`, o `None`.
    """
    try:
        if not settings.news_collection_enabled:
            logger.info("Recoleccion de noticias desactivada (NEWS_COLLECTION_ENABLED no es true).")
            return None
        fetcher = HttpFetcher(
            user_agent=settings.news_user_agent, max_bytes=settings.news_max_response_bytes,
            transport=transport,
        )
        return NewsCollector(db, settings, fetcher)
    except Exception as exc:  # noqa: BLE001 - construirlo jamas impide arrancar el sistema
        logger.error("Recoleccion de noticias NO iniciada (%s); el resto del sistema sigue.",
                     type(exc).__name__)
        return None


async def run_isolated(collector: NewsCollector) -> None:
    """Corre el colector capturando cualquier `Exception` que escape.

    Un bug del colector no se propaga al scheduler, al etiquetado, al brazo ni al monitor.

    Args:
        collector: el colector.
    """
    try:
        await collector.run_forever()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - un bug del colector no se propaga a nadie
        logger.error(
            "El colector de noticias termino de forma inesperada (%s); el scheduler, el "
            "etiquetado, el brazo ejecutado y el monitor siguen funcionando.", type(exc).__name__,
        )


def maybe_start_news_collector(
    settings: Settings, db: Database, tasks: list[asyncio.Task],
) -> NewsCollector | None:
    """Si corresponde, crea la tarea del colector y la agrega a `tasks`.

    Asi el cierre ordenado del `lifespan` la cancela igual que a las demas. Nunca lanza.

    Args:
        settings: configuracion.
        db: conexion a la base.
        tasks: lista de tareas del `lifespan`.

    Returns:
        El colector, o `None` si esta desactivado.

    Efectos:
        Crea una tarea asyncio y agrega una entrada al log.
    """
    collector = build_news_collector(settings, db)
    if collector is None:
        return None
    try:
        tasks.append(asyncio.create_task(run_isolated(collector)))
    except Exception as exc:  # noqa: BLE001
        logger.error("Recoleccion de noticias NO iniciada (%s); el resto del sistema sigue.",
                     type(exc).__name__)
        return None
    logger.info(
        "Recoleccion de noticias iniciada (tarea aislada, sin costo de Anthropic): %s",
        ", ".join(settings.news_sources_list),
    )
    return collector
