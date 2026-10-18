"""Fase 4, API de solo lectura de noticias y memoria (`/api/news/*`, `/api/memory/*`): exige login,
sanea el texto de terceros, lleva las atribuciones, y NO devuelve ningun resultado de operaciones
(lecciones, tasas, retornos, informes con PnL) mientras la puerta (gate de la medicion + registro
oficial del veredicto) este cerrada, ni siquiera si hay lecciones guardadas. La puerta falla
cerrada. App FastAPI minima, nunca la real ni ningun puerto."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.routes import memory as memory_routes
from app.api.routes import news as news_routes
from app.api.security import hash_password
from app.config import Settings
from app.memory import entries, gate, lessons
from app.news.sources import GDELT, SOURCES
from app.persistence.database import Database
from app.persistence.repositories import health_repo, memory_repo, news_repo
from app.persistence.repositories.memory_repo import LessonRow
from app.persistence.repositories.news_repo import NewsRecord
from app.trading.measurement_status import compute_status
from tests.unit.synthetic_measurement import (
    SYNTHETIC_HASH,
    create_schema,
    insert_groups,
    open_read_only,
    scenario,
)
from tests.unit.test_real_money_report import REPORT_TEXT

PASSWORD = "correcta-123"
H = timedelta(hours=1)
HIDDEN = "ocultos hasta el registro oficial del veredicto"


def now() -> datetime:
    """La hora se calcula al EJECUTAR cada prueba, no al importar el modulo: la suite completa
    tarda minutos y las rutas miden las edades contra su propio `datetime.now`."""
    return datetime.now(UTC)


def make_settings(**overrides) -> Settings:
    defaults = {
        "AUTH_USERNAME": "renzo", "AUTH_PASSWORD_HASH": hash_password(PASSWORD),
        "JWT_SECRET": "un-secreto-de-prueba-bien-largo-no-el-real",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_client(
    db: Database, settings: Settings | None = None, *, collector=None, runner=None,
    authenticated: bool = True,
) -> TestClient:
    app = FastAPI()
    app.state.db = db
    app.state.settings = settings or make_settings()
    app.state.news_collector = collector
    app.state.lessons_runner = runner
    app.include_router(auth_routes.router)
    app.include_router(news_routes.router)
    app.include_router(memory_routes.router)
    client = TestClient(app)
    if authenticated:
        login = client.post("/api/auth/login", json={"username": "renzo", "password": PASSWORD})
        assert login.status_code == 200
    return client


def item(n: int, **kw) -> NewsRecord:
    values = {
        "source": GDELT, "fetched_at": now() - H, "published_at": now() - H,
        "title": f"Titular {n}",
        "url": f"https://x.test/{n}", "url_hash": f"u{n}", "title_hash": f"h{n}",
        "domain": "x.test", "assets": ["BTC"], "sentiment": 0.2, "sentiment_method": "LEXICON_V1",
    }
    values.update(kw)
    return NewsRecord(**values)


def lesson_row(pattern="ema_cross_9_21", status="ACTIVE") -> LessonRow:
    return LessonRow(
        build_id="b1", built_at=now(), dimension="strategy_set", pattern=pattern, n=60, wins=50,
        winrate=0.83, ci_low=0.7, ci_high=0.92, base_winrate=0.5, mean_return_pct=2.5,
        half1_delta=0.3, half2_delta=0.32, status=status,
    )


ALL_ROUTES = (
    "/api/news/status", "/api/news/recent", "/api/memory/status", "/api/memory/lessons",
    "/api/memory/reports?kind=POSTMORTEM",
)


@pytest.mark.asyncio
async def test_every_route_requires_a_login(db):
    client = make_client(db, authenticated=False)

    for path in ALL_ROUTES:
        assert client.get(path).status_code == 401, path


# --- noticias -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_news_status_when_the_collector_is_off_says_so_and_keeps_the_attribution(db):
    body = make_client(db).get("/api/news/status").json()

    assert body["enabled"] is False and body["active"] is False
    assert body["sources"] == {} and body["items_24h"] == 0 and body["fear_greed"] is None
    assert len(body["attribution"]) == 3


@pytest.mark.asyncio
async def test_news_status_reports_source_health_items_and_fear_greed(db):
    start = now()
    await health_repo.record_success(
        db, SOURCES[GDELT].health_key, start - 10 * timedelta(minutes=1)
    )
    await health_repo.record_failure(db, SOURCES["fed_monetary"].health_key, "HTTP 503")
    await news_repo.insert_item(db, item(1))
    await news_repo.insert_item(db, item(2, fetched_at=start - 30 * H, published_at=start - 30 * H))
    await news_repo.upsert_fear_greed(
        db, ts=start - 3 * H, value=27, classification="Fear", fetched_at=start - 2 * H
    )
    client = make_client(db, make_settings(NEWS_COLLECTION_ENABLED=True), collector=object())

    body = client.get("/api/news/status").json()

    assert body["enabled"] is True and body["active"] is True
    assert body["items_24h"] == 1  # la de hace 30 h no cuenta
    assert body["sources"][GDELT]["consecutive_failures"] == 0
    assert body["sources"]["fed_monetary"]["consecutive_failures"] == 1
    assert body["sources"]["fed_monetary"]["last_error"] == "HTTP 503"
    assert body["fear_greed"]["value"] == 27 and body["fear_greed"]["classification"] == "Fear"
    assert body["fear_greed"]["age_hours"] == pytest.approx(3.0, abs=0.1)
    assert any("alternative.me" in line.lower() for line in body["attribution"])


@pytest.mark.asyncio
async def test_news_recent_returns_clean_headlines_with_attribution(db):
    await news_repo.insert_item(db, item(1, title="<b>BTC</b> sube​\x00 con fuerza"))
    await news_repo.insert_item(db, item(2, url="javascript:alert(1)"))
    await news_repo.insert_item(db, item(3, assets=["ETH"], title="Ethereum baja"))
    await news_repo.insert_item(db, item(4, dup_of=1, title="Duplicado"))
    await news_repo.insert_item(db, item(5, url="https://X.test/a/?utm_source=z#frag"))

    body = make_client(db).get("/api/news/recent").json()

    titles = {i["title"] for i in body["items"]}
    assert "BTC sube con fuerza" in titles and "Duplicado" not in titles
    assert all("<" not in i["title"] and "​" not in i["title"] for i in body["items"])
    by_title = {i["title"]: i for i in body["items"]}
    assert by_title["Titular 2"]["url"] is None  # un esquema no http(s) nunca sale como enlace
    assert by_title["Titular 5"]["url"] == "https://x.test/a"
    assert body["attribution"] and all(isinstance(line, str) for line in body["attribution"])
    assert set(body["items"][0]) == {
        "id", "source", "title", "domain", "url", "published_at", "fetched_at", "assets",
        "sentiment", "sentiment_method",
    }  # nunca el cuerpo del articulo


@pytest.mark.asyncio
async def test_news_recent_filters_by_symbol_and_validates_its_parameters(db):
    await news_repo.insert_item(db, item(1, assets=["BTC"]))
    await news_repo.insert_item(db, item(2, assets=["ETH"], title="Ethereum"))
    client = make_client(db)

    only_eth = client.get("/api/news/recent?symbol=ETH").json()["items"]

    assert [i["title"] for i in only_eth] == ["Ethereum"]
    assert len(client.get("/api/news/recent?limit=1").json()["items"]) == 1
    for bad in ("limit=0", "limit=101", "symbol=btc", "symbol=BT%25", "symbol=BTC%27%20OR%201"):
        assert client.get(f"/api/news/recent?{bad}").status_code == 422, bad


# --- memoria: conteos siempre, resultados solo con la puerta -------------------------------------


@pytest.fixture
async def memory_db(tmp_path):
    path = tmp_path / "phase4_api.db"
    await create_schema(path)
    return path


async def open_db(path) -> Database:
    db = Database(str(path))
    await db.connect()
    return db


async def seeded_memory(path, *, n_approved=300, n_rejected=300) -> Database:
    """Base sintetica con el gate de la medicion abierto, la memoria ya construida y un
    post-mortem guardado."""
    insert_groups(path, scenario(n_approved, n_rejected, approved_ret=0.30))
    db = await open_db(path)
    stamp = now()
    await entries.refresh_entries(db, now=stamp)
    await lessons.rebuild_lessons(db, min_sample=30, now=stamp)
    await memory_repo.save_report(
        db, kind="POSTMORTEM", created_at=stamp, dedupe_key="s1",
        body={"losses": 4, "total_pnl_net_usdt": -4.2, "in_common": {"side": "LONG"}},
    )
    return db


def patch_gate(monkeypatch, path, docs_dir):
    """La puerta REAL (`is_outcome_reveal_allowed`) sobre la base sintetica: el estado sale de las
    filas sembradas (con el hash sintetico) y el registro oficial se busca en `docs_dir`."""
    async def synthetic_status(_db):
        conn = open_read_only(path)
        try:
            return compute_status(conn, frozen_hash=SYNTHETIC_HASH)
        finally:
            conn.close()

    real = gate.is_outcome_reveal_allowed
    monkeypatch.setattr(gate, "compute_status_async", synthetic_status)
    monkeypatch.setattr(gate, "is_outcome_reveal_allowed", lambda status: real(status, docs_dir))


@pytest.fixture
def docs(tmp_path):
    path = tmp_path / "docs"
    path.mkdir()
    return path


def write_record(docs, verdict="APORTA_VALOR"):
    (docs / "VEREDICTO_3_6_REGISTRO_2026-12-05.txt").write_text(
        REPORT_TEXT.format(verdict=verdict), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_memory_status_gives_only_counts_and_the_switch_while_hidden(db):
    body = make_client(db, runner=None).get("/api/memory/status").json()

    assert body == {
        "enabled": False, "active": False, "min_sample": 30, "closed_reliable": 0, "entries": 0,
        "applications": 0, "last_build_id": None, "results_visible": False,
        "message": f"Lecciones, tasas de acierto y retornos {HIDDEN} (regla del 2026-10-17).",
    }


@pytest.mark.asyncio
async def test_with_the_gate_closed_nothing_with_results_comes_out_even_if_it_is_stored(
    memory_db, monkeypatch, docs,
):
    write_record(docs)  # aunque exista el registro oficial, el gate de la medicion esta cerrado
    db = await seeded_memory(memory_db, n_approved=299, n_rejected=300)
    await memory_repo.replace_lessons(db, "b9", now(), [lesson_row()])
    patch_gate(monkeypatch, memory_db, docs)
    try:
        client = make_client(db)

        status = client.get("/api/memory/status").json()
        lessons_body = client.get("/api/memory/lessons?status=ALL").json()
        reports = client.get("/api/memory/reports?kind=POSTMORTEM").json()
    finally:
        await db.close()

    assert status["results_visible"] is False and status["entries"] == 599
    assert status["closed_reliable"] == 599 and status["last_build_id"] is not None
    assert lessons_body["results_visible"] is False and lessons_body["lessons"] == []
    assert reports["results_visible"] is False and reports["reports"] == []
    everything = json.dumps([status, lessons_body, reports])
    for forbidden in ("winrate", "ci_low", "mean_return", "pnl", "-4.2", "0.83"):
        assert forbidden not in everything, forbidden
    assert HIDDEN in lessons_body["message"] and HIDDEN in reports["message"]


@pytest.mark.asyncio
async def test_gate_open_without_the_official_record_is_still_hidden(
    memory_db, monkeypatch, docs,
):
    db = await seeded_memory(memory_db)  # 300/300 y NINGUN registro en `docs`
    patch_gate(monkeypatch, memory_db, docs)
    try:
        client = make_client(db)

        lessons_body = client.get("/api/memory/lessons?status=ALL").json()
        status = client.get("/api/memory/status").json()
    finally:
        await db.close()

    assert lessons_body["results_visible"] is False and lessons_body["lessons"] == []
    assert status["results_visible"] is False


@pytest.mark.asyncio
async def test_gate_open_and_the_official_record_show_lessons_and_reports(
    memory_db, monkeypatch, docs,
):
    write_record(docs)
    db = await seeded_memory(memory_db)
    patch_gate(monkeypatch, memory_db, docs)
    try:
        client = make_client(db, make_settings(LESSONS_MEMORY_ENABLED=True), runner=object())

        status = client.get("/api/memory/status").json()
        everything = client.get("/api/memory/lessons?status=ALL").json()
        active_only = client.get("/api/memory/lessons").json()
        postmortems = client.get("/api/memory/reports?kind=POSTMORTEM").json()
        calibration = client.get("/api/memory/reports?kind=CALIBRATION").json()
        bad_kind = client.get("/api/memory/reports?kind=OTRO")
        bad_status = client.get("/api/memory/lessons?status=NADA")
        no_kind = client.get("/api/memory/reports")
    finally:
        await db.close()

    assert status["results_visible"] is True and status["message"] is None
    assert status["enabled"] is True and status["active"] is True
    assert everything["results_visible"] is True and everything["lessons"]
    assert {"winrate", "ci_low", "ci_high", "base_winrate", "half1_delta"} <= set(
        everything["lessons"][0])
    assert "no garantiza" in everything["disclaimer"]
    assert {x["status"] for x in active_only["lessons"]} <= {"ACTIVE"}
    assert len(active_only["lessons"]) <= len(everything["lessons"])
    assert postmortems["reports"][0]["body"]["losses"] == 4
    assert calibration["results_visible"] is True
    assert (bad_kind.status_code, bad_status.status_code, no_kind.status_code) == (422, 422, 422)


@pytest.mark.asyncio
async def test_the_gate_fails_closed_when_it_cannot_be_evaluated(db, monkeypatch, caplog):
    async def broken(_db):
        raise RuntimeError("base ilegible con clave=SECRETO")

    monkeypatch.setattr(gate, "compute_status_async", broken)
    await memory_repo.replace_lessons(db, "b1", now(), [lesson_row()])
    client = make_client(db)

    with caplog.at_level("ERROR", logger="app.memory.gate"):
        body = client.get("/api/memory/lessons?status=ALL")

    assert body.status_code == 200
    assert body.json()["results_visible"] is False and body.json()["lessons"] == []
    assert await gate.results_visible(db) is False
    assert any("se mantienen ocultos" in r.getMessage() for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


def test_the_phase4_routes_are_the_only_new_api_files_and_import_no_trading_core():
    import ast
    from pathlib import Path

    routes = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"
    forbidden = ("app.core.scheduler", "app.trading.shadow_live_labeler", "app.execution",
                 "app.trading.risk_engine", "app.trading.paper_execution", "anthropic")
    offenders = []
    for name in ("news.py", "memory.py"):
        tree = ast.parse((routes / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            offenders += [f"{name}: {m}" for m in modules if m.startswith(forbidden)]

    assert offenders == []
