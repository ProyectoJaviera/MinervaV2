"""Subfase 3.9, silencio de resultados de la cuenta paper: con el brazo ejecutado activo,
`trades` contiene operaciones de senales APROBADA (la mitad APROBADA del veredicto). `GET /trades`
y `GET /positions` (sin login) pasan por una lista BLANCA y solo traen PnL, costos, trailing y
motivo de cierre con `is_outcome_reveal_allowed` (gate abierto + registro oficial), igual que
las sombras. Los comandos de Telegram tienen sus propios tests (`test_telegram_commands.py`,
`test_telegram_metrics.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import positions, trades
from app.api.serializers import (
    _TRADE_ALWAYS_VISIBLE_FIELDS,
    _TRADE_OUTCOME_FIELDS,
    trade_to_public_dict,
)
from app.config import Settings
from app.execution.paper_backend import PaperBackend
from app.persistence.models import Side, Trade
from app.persistence.repositories import trades_repo
from app.trading.levels import StrategyLevels
from tests.unit.test_paper_execution import FakeRestClient

LEAK = 4.321  # un PnL inconfundible


def make_app(db) -> FastAPI:
    app = FastAPI()
    app.state.db = db
    app.include_router(trades.router)
    app.include_router(positions.router)
    return app


async def seed(db):
    """Una operacion LLM_APROBADA cerrada con ganancia y otra abierta."""
    settings = Settings(_env_file=None, INITIAL_CAPITAL_USDT=100.0)  # type: ignore[call-arg]
    backend = PaperBackend(db, FakeRestClient(100.0), settings)
    closed = await backend.open_position(
        "BTCUSDT", Side.LONG, 5.0, 10, strategy="ema_cross_9_21", sl_margin_loss_pct=30.0,
        decision_source="LLM_APROBADA", levels=StrategyLevels(97.0, 110.0),
    )
    await backend.close_if_open(closed.id, 100.0 + LEAK, "TP", fill_source="TICK")
    opened = await backend.open_position(
        "ETHUSDT", Side.SHORT, 5.0, 10, strategy="ema_cross_9_21", sl_margin_loss_pct=30.0,
        decision_source="LLM_APROBADA", levels=StrategyLevels(103.0, 90.0),
    )
    return closed, opened


def client(db):
    return TestClient(make_app(db))


async def test_trades_hide_every_result_field_without_the_official_record(db):
    await seed(db)

    rows = client(db).get("/trades").json()

    assert len(rows) == 2
    for row in rows:
        assert set(row) == set(_TRADE_ALWAYS_VISIBLE_FIELDS)
        assert row["decision_source"] == "LLM_APROBADA"
        for hidden in _TRADE_OUTCOME_FIELDS:
            assert hidden not in row
    text = str(rows)
    assert str(LEAK) not in text and "close_reason" not in text and "'TP'" not in text
    closed = next(r for r in rows if r["status"] == "CLOSED")
    assert closed["symbol"] == "BTCUSDT" and "pnl_net_usdt" not in closed
    assert "closed_at" not in closed  # ni siquiera cuando cerro


async def test_positions_hide_costs_and_trailing_state_without_the_official_record(db):
    await seed(db)

    rows = client(db).get("/positions").json()

    assert [r["symbol"] for r in rows] == ["ETHUSDT"]  # solo la abierta
    assert set(rows[0]) == set(_TRADE_ALWAYS_VISIBLE_FIELDS)
    for hidden in ("best_price", "effective_stop", "fee_entry_usdt", "slippage_entry_usdt",
                   "funding_paid_usdt", "pnl_net_usdt"):
        assert hidden not in rows[0]
    assert rows[0]["sl_price"] is not None and rows[0]["entry_price"] == 100.0  # el plan, si


async def test_with_the_gate_open_and_the_official_record_the_results_are_returned(
    db, monkeypatch,
):
    await seed(db)
    monkeypatch.setattr(trades, "is_outcome_reveal_allowed", lambda *a, **k: True)
    monkeypatch.setattr(positions, "is_outcome_reveal_allowed", lambda *a, **k: True)

    closed = next(r for r in client(db).get("/trades").json() if r["status"] == "CLOSED")
    opened = client(db).get("/positions").json()[0]

    assert closed["pnl_net_usdt"] > 0 and closed["close_reason"] == "TP"
    assert closed["exit_price"] == 100.0 + LEAK and closed["closed_at"] is not None
    assert "best_price" in opened and "effective_stop" in opened


async def test_the_whitelist_fails_closed_for_fields_added_to_trade_later(db):
    """Un campo nuevo de `Trade` no aparece en la respuesta hasta agregarlo a mano a la lista."""

    class FutureTrade(Trade):
        secret_edge: float = 123.456

    trade = FutureTrade(
        symbol="BTCUSDT", side=Side.LONG, leverage=10, margin_usdt=5.0, notional_usdt=50.0,
        qty=0.5, entry_price=100.0, opened_at=datetime.now(UTC), pnl_net_usdt=9.99,
        decision_json='{"razonamiento": "x"}', reconciliation_failed=True,
    )

    hidden = trade_to_public_dict(trade, reveal_outcome=False)
    shown = trade_to_public_dict(trade, reveal_outcome=True)

    for public in (hidden, shown):
        assert "secret_edge" not in public and "decision_json" not in public
        assert "reconciliation_failed" not in public
    assert "pnl_net_usdt" not in hidden and shown["pnl_net_usdt"] == 9.99


async def test_the_routes_no_longer_declare_the_trade_model_as_response(db):
    app = make_app(db)

    for route in app.routes:
        if getattr(route, "path", None) in {"/trades", "/positions"}:
            assert route.response_model is None  # `response_model=list[Trade]` no protege nada


async def test_an_empty_account_returns_empty_lists(db):
    assert client(db).get("/trades").json() == []
    assert client(db).get("/positions").json() == []
    assert await trades_repo.get_trades(db) == []
