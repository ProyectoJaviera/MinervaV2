"""Noticias recolectadas (Fase 4, `app/news/`): tablas `news_items`, `news_fetch_state` y
`fear_greed`. Solo titulo, URL, dominio y fecha (nunca el cuerpo). Todo lo que se lee para un
contexto de decision exige `fetched_at <= instante` Y `published_at <= instante`: lo que el bot
todavia no habia visto no existia para esa decision."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from app.persistence.database import Database


@dataclass
class NewsRecord:
    source: str
    fetched_at: datetime
    title: str
    url: str
    url_hash: str
    title_hash: str
    published_at: datetime | None = None
    domain: str | None = None
    language: str | None = None
    source_country: str | None = None
    assets: list[str] = field(default_factory=list)
    query_tag: str | None = None
    sentiment: float | None = None
    sentiment_method: str | None = None
    dup_of: int | None = None
    id: int | None = None


@dataclass(frozen=True)
class FetchState:
    source: str
    etag: str | None = None
    last_modified: str | None = None
    last_attempt_at: datetime | None = None
    next_allowed_at: datetime | None = None
    last_status: int | None = None
    last_items: int | None = None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_record(row) -> NewsRecord:
    return NewsRecord(
        id=row["id"], source=row["source"], fetched_at=datetime.fromisoformat(row["fetched_at"]),
        published_at=_dt(row["published_at"]), domain=row["domain"], title=row["title"],
        url=row["url"], url_hash=row["url_hash"], title_hash=row["title_hash"],
        language=row["language"], source_country=row["source_country"],
        assets=json.loads(row["assets_json"] or "[]"), query_tag=row["query_tag"],
        sentiment=row["sentiment"], sentiment_method=row["sentiment_method"],
        dup_of=row["dup_of"],
    )


async def insert_item(db: Database, rec: NewsRecord) -> int | None:
    """`INSERT OR IGNORE` sobre `url_hash`: el id nuevo, o `None` si la noticia ya existia."""
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO news_items (
            source, fetched_at, published_at, domain, title, url, url_hash, title_hash,
            language, source_country, assets_json, query_tag, sentiment, sentiment_method, dup_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.source, rec.fetched_at.isoformat(),
            rec.published_at.isoformat() if rec.published_at else None, rec.domain, rec.title,
            rec.url, rec.url_hash, rec.title_hash, rec.language, rec.source_country,
            json.dumps(rec.assets), rec.query_tag, rec.sentiment, rec.sentiment_method, rec.dup_of,
        ),
    )
    return cursor.lastrowid if cursor.rowcount == 1 else None


async def get_recent_originals(db: Database, since: datetime) -> list[NewsRecord]:
    """Titulares NO duplicados vistos desde `since` (para detectar casi duplicados al insertar)."""
    rows = await db.fetch_all(
        "SELECT * FROM news_items WHERE dup_of IS NULL AND fetched_at >= ? ORDER BY id",
        (since.isoformat(),),
    )
    return [_row_to_record(r) for r in rows]


async def get_for_context(
    db: Database, *, as_of: datetime, since: datetime, symbol: str | None = None, limit: int = 20,
) -> list[NewsRecord]:
    """Noticias NO duplicadas que existian en `as_of`: publicadas Y vistas por el bot en o antes de
    ese instante, y no mas viejas que `since`. `symbol` filtra por activo detectado en el titular
    (None = todas). Mas recientes primero."""
    query = (
        "SELECT * FROM news_items WHERE dup_of IS NULL AND fetched_at <= ? "
        "AND published_at IS NOT NULL AND published_at <= ? AND published_at >= ?"
    )
    params: list = [as_of.isoformat(), as_of.isoformat(), since.isoformat()]
    if symbol is not None:
        query += " AND assets_json LIKE ?"
        params.append(f'%"{symbol}"%')
    query += " ORDER BY published_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_row_to_record(r) for r in await db.fetch_all(query, tuple(params))]


async def get_recent(
    db: Database, *, limit: int = 50, symbol: str | None = None,
) -> list[NewsRecord]:
    query = "SELECT * FROM news_items WHERE dup_of IS NULL"
    params: list = []
    if symbol is not None:
        query += " AND assets_json LIKE ?"
        params.append(f'%"{symbol}"%')
    query += " ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_row_to_record(r) for r in await db.fetch_all(query, tuple(params))]


async def count_since(db: Database, since: datetime) -> dict[str, int]:
    """Titulares guardados desde `since`, por fuente (excluye duplicados)."""
    rows = await db.fetch_all(
        "SELECT source, COUNT(*) AS n FROM news_items WHERE dup_of IS NULL AND fetched_at >= ? "
        "GROUP BY source",
        (since.isoformat(),),
    )
    return {r["source"]: r["n"] for r in rows}


async def prune_older_than(db: Database, cutoff: datetime) -> int:
    cursor = await db.execute(
        "DELETE FROM news_items WHERE fetched_at < ?", (cutoff.isoformat(),)
    )
    return cursor.rowcount


async def get_state(db: Database, source: str) -> FetchState:
    row = await db.fetch_one("SELECT * FROM news_fetch_state WHERE source = ?", (source,))
    if row is None:
        return FetchState(source=source)
    return FetchState(
        source=source, etag=row["etag"], last_modified=row["last_modified"],
        last_attempt_at=_dt(row["last_attempt_at"]), next_allowed_at=_dt(row["next_allowed_at"]),
        last_status=row["last_status"], last_items=row["last_items"],
    )


async def save_state(db: Database, state: FetchState) -> None:
    await db.execute(
        """
        INSERT INTO news_fetch_state (
            source, etag, last_modified, last_attempt_at, next_allowed_at, last_status, last_items
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source) DO UPDATE SET
            etag = excluded.etag, last_modified = excluded.last_modified,
            last_attempt_at = excluded.last_attempt_at, next_allowed_at = excluded.next_allowed_at,
            last_status = excluded.last_status, last_items = excluded.last_items
        """,
        (
            state.source, state.etag, state.last_modified,
            state.last_attempt_at.isoformat() if state.last_attempt_at else None,
            state.next_allowed_at.isoformat() if state.next_allowed_at else None,
            state.last_status, state.last_items,
        ),
    )


async def upsert_fear_greed(
    db: Database, *, ts: datetime, value: int, classification: str | None, fetched_at: datetime,
) -> bool:
    cursor = await db.execute(
        "INSERT OR IGNORE INTO fear_greed (ts, value, classification, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (ts.isoformat(), value, classification, fetched_at.isoformat()),
    )
    return cursor.rowcount == 1


async def latest_fear_greed(db: Database, as_of: datetime) -> dict | None:
    """El ultimo valor publicado Y visto por el bot en o antes de `as_of`."""
    row = await db.fetch_one(
        "SELECT ts, value, classification, fetched_at FROM fear_greed "
        "WHERE ts <= ? AND fetched_at <= ? ORDER BY ts DESC LIMIT 1",
        (as_of.isoformat(), as_of.isoformat()),
    )
    if row is None:
        return None
    return {
        "ts": row["ts"], "value": row["value"], "classification": row["classification"],
        "fetched_at": row["fetched_at"],
    }
