"""Bloque `news` de `GET /health`: interruptor, si el colector quedo armado, salud por fuente y
titulares de las ultimas 24 h. Solo lectura de `data_source_health` y `news_items`; nunca resultados
de operaciones. Las noticias NO son un dato critico: una fuente caida no bloquea nada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.news.sources import SOURCES, attribution_lines
from app.persistence.database import Database
from app.persistence.repositories import health_repo, news_repo


async def news_health_block(db: Database, settings: Settings, *, active: bool) -> dict:
    """El bloque `news` de `GET /health`: interruptor, salud por fuente y titulares de 24 h.

    Solo lectura; nunca resultados de operaciones. Las noticias NO son un dato critico: una
    fuente caida no bloquea nada.

    Args:
        db: conexion a la base.
        settings: configuracion.
        active: si el colector quedo armado en este proceso.

    Returns:
        Diccionario con `enabled`, `active`, `sources`, `items_24h` y `attribution`.

    Efectos:
        Lee `data_source_health` y `news_items`.
    """
    enabled = settings.news_collection_enabled
    block: dict = {
        "enabled": enabled, "active": active, "sources": {}, "items_24h": 0,
        "attribution": attribution_lines(settings.news_sources_list),
    }
    if not enabled:
        return block
    rows = {r["source"]: r for r in await health_repo.get_health_rows(db, "news:")}
    for source_id in settings.news_sources_list:
        source = SOURCES.get(source_id)
        if source is None:
            continue
        row = rows.get(source.health_key)
        block["sources"][source_id] = {
            "last_success_at": row["last_success_at"] if row else None,
            "consecutive_failures": row["consecutive_failures"] if row else 0,
            "last_error": row["last_error"] if row else None,
        }
    counts = await news_repo.count_since(db, datetime.now(UTC) - timedelta(hours=24))
    block["items_24h"] = sum(counts.values())
    return block
