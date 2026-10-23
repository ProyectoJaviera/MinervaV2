"""Endpoints de solo lectura bajo `/api/` (Subfase C del plan pre-Fase 5
aprobado 2026-10-14): `/api/measurement/status`, `/api/llm/decisions/
recent`, `/api/shadow-trades/recent`. Todos protegidos con `require_auth`;
el de sombras nunca revela PnL hasta que el gate de medicion abre. App
FastAPI minima (auth + estas 3 rutas + `app.state.db`/`settings`), nunca la
app real ni ningun puerto."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.routes import llm_decisions, measurement, shadow_trades
from app.api.security import hash_password
from app.config import Settings
from app.persistence.database import Database
from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.persistence.repositories import llm_logs_repo, shadow_repo
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256

VALID_PASSWORD = "correcta-123"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make_settings(**overrides) -> Settings:
    defaults = {
        "AUTH_USERNAME": "admin", "AUTH_PASSWORD_HASH": hash_password(VALID_PASSWORD),
        "JWT_SECRET": "un-secreto-de-prueba-bien-largo-no-el-real",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_app(db: Database, settings: Settings | None = None) -> FastAPI:
    app = FastAPI()
    app.state.db = db
    app.state.settings = settings or make_settings()
    app.include_router(auth_routes.router)
    app.include_router(measurement.router)
    app.include_router(llm_decisions.router)
    app.include_router(shadow_trades.router)
    return app


def make_authenticated_client(db: Database, settings: Settings | None = None) -> TestClient:
    client = TestClient(make_app(db, settings))
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": VALID_PASSWORD}
    )
    assert login.status_code == 200
    return client


async def _seed_group(
    db, *, key, symbol="BTCUSDT", side=Side.LONG, opened_at, closed_at=None,
    candle_close_time=None, llm_decision=None, log=None,
):
    trade = ShadowTrade(
        symbol=symbol, side=side, strategy="ema_cross_9_21", status=TradeStatus.OPEN,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0,
        opened_at=opened_at, contributing_strategies=["ema_cross_9_21"], signal_group_key=key,
        candle_close_time=candle_close_time or opened_at,
    )
    trade_id = await shadow_repo.insert_if_new(db, trade)
    if closed_at is not None:
        await shadow_repo.close(
            db, trade_id, exit_price=105.0, fee_exit_usdt=0.0, slippage_exit_usdt=0.0,
            pnl_gross_usdt=5.0, pnl_net_usdt=4.5, close_reason="TP", fill_source="TICK",
            closed_at=closed_at,
        )
    if llm_decision is not None:
        await shadow_repo.set_llm_decision(db, trade_id, llm_decision)
    if log is not None:
        await llm_logs_repo.insert(
            db, signal_group_key=key, shadow_trade_id=trade_id,
            fase=log.get("fase", "MEDICION"), candle_close_time=candle_close_time or opened_at,
            decision_delay_s=5.0, hour_utc=opened_at.hour, atr_pct=None,
            model="claude-sonnet-5-5", prompt_version="signal_review_v4",
            prompt_sha256=log.get("prompt_sha256", FROZEN_PROMPT_SHA256), prompt="p",
            response_raw=log.get("response_raw"), status=log.get("status", "OK"), error=None,
            decision=log.get("decision"), input_tokens=log.get("input_tokens", 100),
            output_tokens=log.get("output_tokens", 20), cost_usd=log.get("cost_usd", 0.001),
            latency_ms=log.get("latency_ms", 500), created_at=log.get("created_at", opened_at),
            json_repaired=log.get("json_repaired", False),
        )
    return trade_id


# --- auth: las 3 rutas exigen login ---------------------------------------------


@pytest.mark.asyncio
async def test_all_three_routes_require_auth(db):
    client = TestClient(make_app(db))

    assert client.get("/api/measurement/status").status_code == 401
    assert client.get("/api/llm/decisions/recent").status_code == 401
    assert client.get("/api/shadow-trades/recent").status_code == 401


# --- GET /api/measurement/status -------------------------------------------------


@pytest.mark.asyncio
async def test_measurement_status_reflects_seeded_data(db):
    await _seed_group(
        db, key="g1", opened_at=T0, llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": T0},
    )
    await _seed_group(
        db, key="g2", opened_at=T0 + timedelta(hours=1),
        log={"status": "TIMEOUT", "decision": None, "created_at": T0 + timedelta(hours=1)},
    )
    client = make_authenticated_client(db)

    response = client.get("/api/measurement/status")

    assert response.status_code == 200
    body = response.json()
    assert body["frozen_hash"] == FROZEN_PROMPT_SHA256
    assert body["ok_by_label"] == {"APROBADA": 1, "RECHAZADA": 0}
    assert body["non_ok_counts"]["TIMEOUT"] == 1
    assert body["gate_open"] is False  # muy lejos de MIN_EFFECTIVE_N


@pytest.mark.asyncio
async def test_measurement_status_on_an_empty_database(db):
    client = make_authenticated_client(db)

    response = client.get("/api/measurement/status")

    assert response.status_code == 200
    assert response.json()["total_medicion_rows"] == 0
    assert response.json()["gate_open"] is False


# --- GET /api/llm/decisions/recent -----------------------------------------------


@pytest.mark.asyncio
async def test_recent_decisions_includes_reasoning_for_ok_rows(db):
    razonamiento = "riesgo aceptable, tendencia a favor"
    text = f'{{"decision": "APROBAR", "confianza": 0.7, "razonamiento": "{razonamiento}"}}'
    await _seed_group(
        db, key="g1", opened_at=T0, llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": T0, "response_raw": text},
    )
    client = make_authenticated_client(db)

    response = client.get("/api/llm/decisions/recent")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["decision"] == "APROBAR"
    assert rows[0]["razonamiento"] == razonamiento
    assert rows[0]["confianza"] == 0.7
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["side"] == "LONG"
    assert "prompt" not in rows[0]
    assert "response_raw" not in rows[0]


@pytest.mark.asyncio
async def test_recent_decisions_has_no_reasoning_for_non_ok_rows(db):
    await _seed_group(
        db, key="g1", opened_at=T0,
        log={"status": "TIMEOUT", "decision": None, "created_at": T0},
    )
    client = make_authenticated_client(db)

    response = client.get("/api/llm/decisions/recent")

    rows = response.json()
    assert rows[0]["status"] == "TIMEOUT"
    assert rows[0]["razonamiento"] is None
    assert rows[0]["confianza"] is None


@pytest.mark.asyncio
async def test_recent_decisions_respects_the_limit_param(db):
    for i in range(3):
        await _seed_group(
            db, key=f"g{i}", opened_at=T0 + timedelta(hours=i), llm_decision="APROBADA",
            log={"decision": "APROBAR", "created_at": T0 + timedelta(hours=i)},
        )
    client = make_authenticated_client(db)

    response = client.get("/api/llm/decisions/recent?limit=2")

    assert len(response.json()) == 2


# --- GET /api/shadow-trades/recent: nunca revela PnL con el gate cerrado -------


@pytest.mark.asyncio
async def test_recent_shadows_never_include_outcome_fields_with_the_gate_closed(db):
    await _seed_group(
        db, key="g1", opened_at=T0, closed_at=T0 + timedelta(hours=1),
        llm_decision="APROBADA", log={"decision": "APROBAR", "created_at": T0},
    )
    client = make_authenticated_client(db)

    response = client.get("/api/shadow-trades/recent")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    for field in ("pnl_gross_usdt", "pnl_net_usdt", "exit_price", "close_reason", "closed_at"):
        assert field not in rows[0]
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["llm_decision"] == "APROBADA"


VALID_RECORD = (
    "=== Veredicto de valor de la IA (subfase 3.6) ===\n"
    "- **Veredicto: INCONCLUSO** (el intervalo cruza entre cero y delta)\n"
)


async def _seed_two_closed_groups(db):
    await _seed_group(
        db, key="g1", opened_at=T0, closed_at=T0 + timedelta(hours=1),
        llm_decision="APROBADA", log={"decision": "APROBAR", "created_at": T0},
    )
    await _seed_group(
        db, key="g2", opened_at=T0 + timedelta(hours=2), closed_at=T0 + timedelta(hours=3),
        llm_decision="RECHAZADA",
        log={"decision": "RECHAZAR", "created_at": T0 + timedelta(hours=2)},
    )


def _open_gate(monkeypatch):
    """Fuerza el gate abierto bajando MIN_EFFECTIVE_N a 1 (en vez de sembrar 300
    conglomerados reales). Solo `measurement_status` lee esa constante para el gate."""
    import app.trading.measurement_status as ms_module

    monkeypatch.setattr(ms_module, "MIN_EFFECTIVE_N", 1)


def _docs_with(monkeypatch, tmp_path, files: dict[str, str]):
    from app.trading import verdict_record

    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(verdict_record, "DOCS_DIR", tmp_path)


OUTCOME_FIELDS = ("pnl_gross_usdt", "pnl_net_usdt", "exit_price", "close_reason", "closed_at")


@pytest.mark.asyncio
async def test_recent_shadows_include_outcome_fields_only_with_the_gate_open_and_the_record(
    db, monkeypatch, tmp_path,
):
    _open_gate(monkeypatch)
    _docs_with(monkeypatch, tmp_path, {"VEREDICTO_3_6_REGISTRO_2026-12-05.txt": VALID_RECORD})
    await _seed_two_closed_groups(db)
    client = make_authenticated_client(db)

    rows = client.get("/api/shadow-trades/recent").json()

    approved_row = next(r for r in rows if r["llm_decision"] == "APROBADA")
    assert approved_row["pnl_net_usdt"] == 4.5
    assert approved_row["close_reason"] == "TP"
    status = client.get("/api/measurement/status").json()
    assert status["gate_open"] is True and status["outcome_reveal_allowed"] is True


@pytest.mark.asyncio
async def test_gate_open_without_the_official_record_reveals_nothing(db, monkeypatch, tmp_path):
    _open_gate(monkeypatch)
    _docs_with(monkeypatch, tmp_path, {})  # docs/ sin registro
    await _seed_two_closed_groups(db)
    client = make_authenticated_client(db)

    rows = client.get("/api/shadow-trades/recent").json()
    status = client.get("/api/measurement/status").json()

    assert rows and all(field not in row for row in rows for field in OUTCOME_FIELDS)
    assert status["gate_open"] is True and status["outcome_reveal_allowed"] is False


@pytest.mark.asyncio
async def test_gate_open_with_a_record_without_a_valid_verdict_reveals_nothing(
    db, monkeypatch, tmp_path,
):
    _open_gate(monkeypatch)
    _docs_with(
        monkeypatch, tmp_path,
        {
            "VEREDICTO_3_6_REGISTRO_2026-12-05.txt": "Calibrado: NO INFORMATIVO",
            "PLACEBO_2026-12-05.txt": VALID_RECORD,  # nombre de placebo: no cuenta
        },
    )
    await _seed_two_closed_groups(db)
    client = make_authenticated_client(db)

    rows = client.get("/api/shadow-trades/recent").json()

    assert rows and all(field not in row for row in rows for field in OUTCOME_FIELDS)
    assert client.get("/api/measurement/status").json()["outcome_reveal_allowed"] is False


@pytest.mark.asyncio
async def test_a_valid_record_with_the_gate_closed_reveals_nothing(db, monkeypatch, tmp_path):
    # El gate real exige 300 por lado: sin tocar la constante sigue cerrado.
    _docs_with(monkeypatch, tmp_path, {"VEREDICTO_3_6_REGISTRO_2026-12-05.txt": VALID_RECORD})
    await _seed_two_closed_groups(db)
    client = make_authenticated_client(db)

    rows = client.get("/api/shadow-trades/recent").json()
    status = client.get("/api/measurement/status").json()

    assert rows and all(field not in row for row in rows for field in OUTCOME_FIELDS)
    assert status["gate_open"] is False and status["outcome_reveal_allowed"] is False


@pytest.mark.asyncio
async def test_the_record_is_read_on_every_request_so_it_unlocks_without_a_restart(
    db, monkeypatch, tmp_path,
):
    _open_gate(monkeypatch)
    _docs_with(monkeypatch, tmp_path, {})
    await _seed_two_closed_groups(db)
    client = make_authenticated_client(db)
    before = client.get("/api/shadow-trades/recent").json()

    (tmp_path / "VEREDICTO_3_6_REGISTRO_2026-12-05.txt").write_text(VALID_RECORD, encoding="utf-8")
    after = client.get("/api/shadow-trades/recent").json()

    assert all("pnl_net_usdt" not in r for r in before)
    assert any("pnl_net_usdt" in r for r in after)
