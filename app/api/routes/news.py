"""Noticias recolectadas (Fase 4), de solo lectura. Titulos, dominio, fecha y enlace: nunca el
cuerpo del articulo. Los titulares son TEXTO NO CONFIABLE de terceros: se vuelven a sanear aqui, y
el enlace solo sale si es http(s). Lleva las atribuciones de las fuentes (GDELT, Reserva Federal,
Alternative.me), que el dashboard debe mostrar. Nada de resultados de operaciones. Protegido con
`require_auth` a nivel de router."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db, get_news_collector, get_settings
from app.api.security import require_auth
from app.config import Settings
from app.news.collector import NewsCollector
from app.news.health import news_health_block
from app.news.sources import attribution_lines
from app.news.text import canonical_url, sanitize_headline
from app.persistence.database import Database
from app.persistence.repositories import news_repo
from app.persistence.repositories.news_repo import NewsRecord

router = APIRouter(prefix="/api/news", dependencies=[Depends(require_auth)])


def news_item_to_public_dict(item: NewsRecord) -> dict:
    """Un titular para la API: saneado de nuevo y con el enlace solo si es http(s).

    Args:
        item: el titular.

    Returns:
        El diccionario serializable (nunca el cuerpo del articulo).
    """
    return {
        "id": item.id,
        "source": item.source,
        "title": sanitize_headline(item.title),
        "domain": item.domain,
        "url": canonical_url(item.url),  # None si no es un enlace http(s) valido
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "fetched_at": item.fetched_at.isoformat(),
        "assets": item.assets,
        "sentiment": item.sentiment,
        "sentiment_method": item.sentiment_method,
    }


@router.get("/status")
async def status(
    db: Database = Depends(get_db), settings: Settings = Depends(get_settings),
    collector: NewsCollector | None = Depends(get_news_collector),
) -> dict:
    """`GET /api/news/status`: interruptor, salud por fuente, titulares de 24 h y Fear & Greed.

    Args:
        db: base (inyectada).
        settings: configuracion (inyectada).
        collector: colector de noticias (inyectado), o `None` si esta apagado.

    Returns:
        El bloque de salud de noticias mas el ultimo Fear & Greed.

    Efectos:
        Lee `data_source_health`, `news_items` y `fear_greed`.
    """
    block = await news_health_block(db, settings, active=collector is not None)
    now = datetime.now(UTC)
    fear_greed = await news_repo.latest_fear_greed(db, now)
    block["fear_greed"] = None if fear_greed is None else {
        "value": fear_greed["value"], "classification": fear_greed["classification"],
        "ts": fear_greed["ts"],
        "age_hours": round(
            (now - datetime.fromisoformat(fear_greed["ts"])).total_seconds() / 3600, 1
        ),
    }
    return block


@router.get("/recent")
async def recent(
    limit: int = Query(default=20, ge=1, le=100),
    symbol: str | None = Query(default=None, pattern=r"^[A-Z0-9]{1,12}$"),
    db: Database = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    """`GET /api/news/recent`: titulares recientes sin duplicados, los mas nuevos primero.

    Args:
        limit: cuantos devolver (1-100).
        symbol: ticker base (`BTC`) para filtrar por activo, o `None`.
        db: base (inyectada).
        settings: configuracion (inyectada).

    Returns:
        Los titulares y las lineas de atribucion de las fuentes.

    Efectos:
        Lee `news_items`.
    """
    items = await news_repo.get_recent(db, limit=limit, symbol=symbol)
    return {
        "items": [news_item_to_public_dict(i) for i in items],
        "attribution": attribution_lines(settings.news_sources_list),
    }
