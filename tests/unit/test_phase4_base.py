"""Fase 4, base: interruptores apagados por defecto (la recoleccion de noticias solo la activa
`start_bot.ps1` y solo con el puerto 8000; la memoria de lecciones nunca), tablas aditivas
e idempotentes y repositorios (noticias sin mirar al futuro, memoria inmutable)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.persistence.database import Database
from app.persistence.repositories import memory_repo, news_repo
from app.persistence.repositories.memory_repo import LessonRow, MemoryEntry
from app.persistence.repositories.news_repo import FetchState, NewsRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
NEW_TABLES = {
    "news_items", "news_fetch_state", "fear_greed", "memory_entries", "lessons",
    "lesson_applications", "lessons_reports",
}


# --- interruptores -------------------------------------------------------------------------


def test_everything_is_off_by_default():
    s = Settings(_env_file=None)  # type: ignore[call-arg]

    assert s.news_collection_enabled is False
    assert s.lessons_memory_enabled is False
    assert s.news_sources_list == ["gdelt", "fed_monetary", "fear_greed"]
    assert s.lessons_min_sample == 30 and s.news_retention_days == 180


def test_the_switches_are_independent_and_follow_the_env_vars():
    news = Settings(_env_file=None, NEWS_COLLECTION_ENABLED=True)  # type: ignore[call-arg]
    memory = Settings(_env_file=None, LESSONS_MEMORY_ENABLED=True)  # type: ignore[call-arg]

    assert news.news_collection_enabled and not news.lessons_memory_enabled
    assert memory.lessons_memory_enabled and not memory.news_collection_enabled


def test_news_sources_are_normalized():
    s = Settings(_env_file=None, NEWS_SOURCES=" GDELT , ,Fed_Monetary")  # type: ignore[call-arg]

    assert s.news_sources_list == ["gdelt", "fed_monetary"]


def test_start_bot_activates_the_news_only_for_port_8000_and_never_the_memory():
    text = (REPO_ROOT / "scripts" / "start_bot.ps1").read_text(encoding="utf-8")

    assert '$newsCollection = if ($isRealBot) { "true" } else { "false" }' in text
    assert "$env:NEWS_COLLECTION_ENABLED = $newsCollection" in text
    assert '$env:LESSONS_MEMORY_ENABLED = "false"' in text
    assert "NEWS_COLLECTION_ENABLED=$newsCollection LESSONS_MEMORY_ENABLED=false" in text
    assert 'NEWS_COLLECTION_ENABLED = "true"' not in text
    assert 'LESSONS_MEMORY_ENABLED = "true"' not in text


def test_the_dev_server_and_the_env_example_keep_both_off():
    dev = (REPO_ROOT / "scripts" / "run_dev_server.py").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()

    assert 'os.environ["NEWS_COLLECTION_ENABLED"] = "false"' in dev
    assert 'os.environ["LESSONS_MEMORY_ENABLED"] = "false"' in dev
    assert "NEWS_COLLECTION_ENABLED=false" in example
    assert "LESSONS_MEMORY_ENABLED=false" in example


# --- tablas aditivas ------------------------------------------------------------------------


def _tables(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        names = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}
    finally:
        conn.close()


async def test_a_database_from_before_phase_4_gets_only_the_new_tables(tmp_path):
    path = tmp_path / "old_shape.db"
    database = Database(str(path))
    await database.connect()
    await database.close()
    conn = sqlite3.connect(str(path))
    for table in NEW_TABLES:
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()
    before = _tables(path)
    assert not NEW_TABLES & set(before)

    for _ in range(2):  # un segundo arranque debe ser inocuo
        reopened = Database(str(path))
        await reopened.connect()
        await reopened.close()

    after = _tables(path)
    assert set(after) - set(before) == NEW_TABLES
    assert all(after.pop(t) == 0 for t in NEW_TABLES)
    assert after == before


REAL_DB = REPO_ROOT / "data" / "minerva_estabilidad.db"


@pytest.mark.skipif(not REAL_DB.exists(), reason="no hay copia local de la base real")
async def test_the_migration_on_a_copy_of_the_real_database_changes_nothing_else(tmp_path):
    copy = tmp_path / "real_copy.db"
    assert copy.resolve() != REAL_DB.resolve()
    source = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(str(copy))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    before = _tables(copy)

    for _ in range(2):
        database = Database(str(copy))
        await database.connect()
        await database.close()

    after = _tables(copy)
    assert set(after) - set(before) <= NEW_TABLES
    assert all(after[t] == 0 for t in NEW_TABLES if t not in before)
    assert {k: v for k, v in after.items() if k in before} == before
    conn = sqlite3.connect(str(copy))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


# --- noticias --------------------------------------------------------------------------------


def news(n: int, *, fetched=None, published=None, assets=("BTC",), dup_of=None, source="gdelt"):
    return NewsRecord(
        source=source, fetched_at=fetched or T0, published_at=published or T0,
        title=f"Titular {n}", url=f"https://example.com/{n}", url_hash=f"u{n}",
        title_hash=f"t{n}", domain="example.com", assets=list(assets), dup_of=dup_of,
        sentiment=0.2, sentiment_method="LEXICON_V1",
    )


async def test_the_same_url_is_never_stored_twice(db):
    first = await news_repo.insert_item(db, news(1))
    second = await news_repo.insert_item(db, news(1))

    assert first is not None and second is None
    assert len(await news_repo.get_recent(db)) == 1


async def test_the_context_query_never_looks_into_the_future(db):
    as_of = T0 + timedelta(hours=2)
    await news_repo.insert_item(db, news(1, published=T0, fetched=T0 + timedelta(minutes=5)))
    # publicada antes pero vista por el bot DESPUES: no existia para esta decision
    await news_repo.insert_item(db, news(2, published=T0, fetched=as_of + timedelta(minutes=1)))
    # publicada despues del instante
    await news_repo.insert_item(db, news(3, published=as_of + timedelta(minutes=1), fetched=T0))
    # justo en el instante: cuenta
    await news_repo.insert_item(db, news(4, published=as_of, fetched=as_of))
    # demasiado vieja para la ventana
    await news_repo.insert_item(db, news(5, published=T0 - timedelta(days=3), fetched=T0))

    items = await news_repo.get_for_context(db, as_of=as_of, since=T0 - timedelta(hours=1))

    assert [i.title for i in items] == ["Titular 4", "Titular 1"]


async def test_the_context_query_excludes_duplicates_and_filters_by_asset(db):
    await news_repo.insert_item(db, news(1, assets=("BTC",)))
    await news_repo.insert_item(db, news(2, assets=("ETH",)))
    await news_repo.insert_item(db, news(3, assets=("BTC",), dup_of=1))

    btc = await news_repo.get_for_context(
        db, as_of=T0 + timedelta(hours=1), since=T0 - timedelta(hours=1), symbol="BTC",
    )

    assert [i.title for i in btc] == ["Titular 1"]
    assert {i.url_hash for i in await news_repo.get_recent(db)} == {"u1", "u2"}


async def test_counts_by_source_and_pruning(db):
    await news_repo.insert_item(db, news(1, source="gdelt"))
    await news_repo.insert_item(db, news(2, source="fed_monetary"))
    await news_repo.insert_item(db, news(3, source="gdelt", fetched=T0 - timedelta(days=200)))

    assert await news_repo.count_since(db, T0 - timedelta(days=1)) == {
        "gdelt": 1, "fed_monetary": 1,
    }
    assert await news_repo.prune_older_than(db, T0 - timedelta(days=180)) == 1
    assert len(await news_repo.get_recent(db)) == 2


async def test_fetch_state_roundtrip_and_default(db):
    assert await news_repo.get_state(db, "gdelt") == FetchState(source="gdelt")

    state = FetchState(
        source="fed_monetary", etag='W/"x"', last_modified="Tue, 13 Oct 2026 18:00:13 GMT",
        last_attempt_at=T0, next_allowed_at=T0 + timedelta(hours=1), last_status=200, last_items=7,
    )
    await news_repo.save_state(db, state)
    await news_repo.save_state(db, state)  # idempotente

    assert await news_repo.get_state(db, "fed_monetary") == state


async def test_fear_greed_returns_the_latest_value_the_bot_had_already_seen(db):
    day = datetime(2026, 11, 1, tzinfo=UTC)
    assert await news_repo.upsert_fear_greed(
        db, ts=day, value=55, classification="Greed", fetched_at=day + timedelta(hours=1),
    )
    assert not await news_repo.upsert_fear_greed(
        db, ts=day, value=99, classification="x", fetched_at=day,
    )  # no se pisa
    await news_repo.upsert_fear_greed(
        db, ts=day + timedelta(days=1), value=40, classification="Fear",
        fetched_at=day + timedelta(days=1, hours=1),
    )

    assert await news_repo.latest_fear_greed(db, day) is None  # aun no lo habia visto
    seen = await news_repo.latest_fear_greed(db, day + timedelta(hours=2))
    assert seen["value"] == 55 and seen["classification"] == "Greed"
    assert (await news_repo.latest_fear_greed(db, day + timedelta(days=3)))["value"] == 40


# --- memoria ----------------------------------------------------------------------------------


def entry(n: int, *, closed=None, outcome="WIN", ret=5.0) -> MemoryEntry:
    closed = closed or T0 + timedelta(hours=n)
    return MemoryEntry(
        signal_group_key=f"BTCUSDT|LONG|{n}", shadow_trade_id=n, symbol="BTCUSDT", side="LONG",
        strategy_set="ema_cross_9_21", timeframe="4h", decision_time=closed - timedelta(hours=3),
        closed_at=closed, hour_utc=8, atr_pct=1.2, risk_reward=1.8, sl_margin_loss_pct=30.0,
        llm_label="APROBADA", news_sentiment=None, news_count=None, fear_greed=None,
        pnl_net_usdt=ret / 100 * 5.0, margin_usdt=5.0, return_pct=ret, outcome=outcome,
        close_reason="TP", duration_hours=3.0,
    )


async def test_memory_entries_are_immutable(db):
    assert await memory_repo.insert_entry(db, entry(1, ret=5.0), built_at=T0)
    assert not await memory_repo.insert_entry(db, entry(1, ret=-99.0, outcome="LOSS"), built_at=T0)

    [stored] = await memory_repo.get_entries_closed_before(db)
    assert stored.return_pct == 5.0 and stored.outcome == "WIN"
    assert stored.strategies == frozenset({"ema_cross_9_21"})


async def test_closed_before_is_strictly_before_the_cutoff(db):
    for n in (1, 2, 3):
        await memory_repo.insert_entry(db, entry(n), built_at=T0)
    cutoff = T0 + timedelta(hours=2)  # entry(2) cierra EXACTAMENTE aqui

    before = await memory_repo.get_entries_closed_before(db, cutoff)

    assert [e.signal_group_key for e in before] == ["BTCUSDT|LONG|1"]
    assert await memory_repo.count_entries(db) == 3
    assert await memory_repo.get_known_keys(db) == {f"BTCUSDT|LONG|{n}" for n in (1, 2, 3)}


async def test_lessons_are_stored_per_build_and_the_latest_is_returned(db):
    old = LessonRow("side", "LONG", 40, 24, 0.6, 0.45, 0.74, 0.5, 2.0, 0.1, 0.12, "ACTIVE")
    new = LessonRow("side", "SHORT", 10, 3, 0.3, 0.1, 0.6, 0.5, -1.0, None, None, "INSUFFICIENT")
    await memory_repo.replace_lessons(db, "b1", T0, [old])
    await memory_repo.replace_lessons(db, "b2", T0 + timedelta(days=1), [new, old])

    latest = await memory_repo.get_lessons(db)
    assert {(x.pattern, x.status) for x in latest} == {
        ("SHORT", "INSUFFICIENT"), ("LONG", "ACTIVE"),
    }
    assert [x.pattern for x in await memory_repo.get_lessons(db, "b1")] == ["LONG"]
    assert [x.pattern for x in await memory_repo.get_lessons(db, status="ACTIVE")] == ["LONG"]


async def test_lessons_on_an_empty_database_are_an_empty_list(db):
    assert await memory_repo.get_lessons(db) == []
    assert await memory_repo.latest_build_id(db) is None


async def test_applications_and_reports_are_idempotent(db):
    kwargs = dict(
        signal_group_key="k1", created_at=T0, build_id="b1", lesson_ids=[1, 2], n_similar=12,
        evidence="BAJA",
    )
    assert await memory_repo.record_application(db, **kwargs)
    assert not await memory_repo.record_application(db, **kwargs)
    assert await memory_repo.count_applications(db) == 1

    assert await memory_repo.save_report(
        db, kind="POSTMORTEM", body={"losses": 3}, created_at=T0, dedupe_key="breaker-1",
    )
    assert not await memory_repo.save_report(
        db, kind="POSTMORTEM", body={"losses": 4}, created_at=T0, dedupe_key="breaker-1",
    )
    [report] = await memory_repo.get_reports(db, "POSTMORTEM")
    assert report["body"] == {"losses": 3} and report["dedupe_key"] == "breaker-1"


async def test_no_phase_4_table_stores_a_raw_article_body(db):
    columns = {
        r["name"] for r in await db.fetch_all("PRAGMA table_info(news_items)")
    }

    assert not columns & {"body", "content", "text", "summary", "description"}
