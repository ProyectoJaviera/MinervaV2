"""Contexto de noticias para una decision FUTURA (prompt `signal_review_v5`, ver
`app/llm/prompts_v5.py`; nada de esto entra al prompt congelado de la medicion).

Reglas:
- **Sin mirar al futuro**: solo noticias publicadas Y vistas por el bot en o antes de `as_of`.
- **Texto NO confiable**: cada titular se vuelve a sanear, se acota y viaja como cadena de un JSON
  (la serializacion la escapa); el bloque va marcado `untrusted_text: true`. El prompt debe tratarlo
  como dato a evaluar, jamas como instruccion.
- **Degradacion controlada**: sin titulares recientes o con las fuentes caidas, el contexto es
  `None` con un estado (`NO_DATA` / `STALE`); nada se inventa.
- Atribucion: lleva los textos de atribucion de las fuentes de las que salio algo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.news.assets import base_ticker
from app.news.sources import FED_MONETARY, SOURCES
from app.news.text import sanitize_headline
from app.persistence.database import Database
from app.persistence.repositories import health_repo, news_repo
from app.persistence.repositories.news_repo import NewsRecord

STATUS_OK = "OK"
STATUS_NO_DATA = "NO_DATA"
STATUS_STALE = "STALE"

CONTEXT_TITLE_CHARS = 160
# Los comunicados de la Fed son escasos (un FOMC cada ~6 semanas): con 72 h el bloque macro estaria
# casi siempre vacio. 14 dias deja ver el ultimo comunicado/acta vigente; cada titular lleva su edad
# (`minutes_ago`) para que el modelo la pese.
MACRO_WINDOW = timedelta(days=14)
MACRO_MAX_ITEMS = 3
LIVE_TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class NewsContext:
    status: str
    context: dict | None
    reason: str | None = None


def _headline(item: NewsRecord, as_of: datetime) -> dict:
    published = item.published_at or item.fetched_at
    return {
        "title": sanitize_headline(item.title, CONTEXT_TITLE_CHARS),
        "domain": item.domain,
        "minutes_ago": max(0, int((as_of - published).total_seconds() // 60)),
        "sentiment": None if item.sentiment is None else round(item.sentiment, 2),
        "assets": item.assets,
    }


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 3) if clean else None


async def _live_sources_down(db: Database, now: datetime, stale_after: timedelta) -> bool:
    """Solo para llamadas "en vivo": TODAS las fuentes de titulares con fallo reciente o sin exito
    en `stale_after`. Una copia historica no tiene este dato: se omite el chequeo."""
    rows = await health_repo.get_health_rows(db, "news:")
    headline_keys = {s.health_key for s in SOURCES.values() if s.kind == "headlines"}
    relevant = [r for r in rows if r["source"] in headline_keys]
    if not relevant:
        return False  # nunca corrio el colector: lo dira NO_DATA
    for row in relevant:
        last = row["last_success_at"]
        fresh = last is not None and now - datetime.fromisoformat(last) <= stale_after
        if fresh and row["consecutive_failures"] == 0:
            return False
    return True


async def build_news_context(
    db: Database, symbol: str, as_of: datetime, *, window_hours: float = 24.0, max_items: int = 5,
    stale_after_hours: float = 6.0, check_live_health: bool | None = None,
) -> NewsContext:
    """Titulares del activo, del mercado y de la Fed disponibles en `as_of`."""
    as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    window = timedelta(hours=window_hours)
    base = base_ticker(symbol)
    now = datetime.now(UTC)
    live = abs(now - as_of) <= LIVE_TOLERANCE if check_live_health is None else check_live_health
    if live and await _live_sources_down(db, now, timedelta(hours=stale_after_hours)):
        return NewsContext(STATUS_STALE, None, "todas las fuentes de titulares estan caidas")

    asset_items = await news_repo.get_for_context(
        db, as_of=as_of, since=as_of - window, symbol=base, limit=max_items,
        exclude_source=FED_MONETARY,
    )
    market_items = await news_repo.get_for_context(
        db, as_of=as_of, since=as_of - window, limit=max_items, untagged_only=True,
        exclude_source=FED_MONETARY,
    )
    macro_items = await news_repo.get_for_context(
        db, as_of=as_of, since=as_of - MACRO_WINDOW, limit=MACRO_MAX_ITEMS, source=FED_MONETARY,
    )
    fear_greed = await news_repo.latest_fear_greed(db, as_of)

    if not (asset_items or market_items or macro_items) and fear_greed is None:
        return NewsContext(STATUS_NO_DATA, None, "sin titulares ni Fear & Greed en la ventana")

    used_sources = {i.source for i in (*asset_items, *market_items, *macro_items)}
    attribution = [SOURCES[s].attribution for s in SOURCES if s in used_sources]
    if fear_greed is not None:
        attribution.append(SOURCES["fear_greed"].attribution)
    context: dict = {
        "untrusted_text": True,
        "as_of": as_of.isoformat(),
        "asset": base,
        "asset_headlines": [_headline(i, as_of) for i in asset_items],
        "asset_headline_count": len(asset_items),
        "asset_sentiment_mean": _mean([i.sentiment for i in asset_items]),
        "market_headlines": [_headline(i, as_of) for i in market_items],
        "market_sentiment_mean": _mean([i.sentiment for i in market_items]),
        "macro_headlines": [_headline(i, as_of) for i in macro_items],
        "fear_greed": None if fear_greed is None else {
            "value": fear_greed["value"], "classification": fear_greed["classification"],
            "age_hours": round(
                (as_of - datetime.fromisoformat(fear_greed["ts"])).total_seconds() / 3600, 1
            ),
        },
        "attribution": attribution,
    }
    return NewsContext(STATUS_OK, context)
