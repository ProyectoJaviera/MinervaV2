"""Fase 5, API de lectura del dashboard: cuenta paper, registro del brazo ejecutado, estado de
riesgo, universo vigente y veredictos de backtest. Todas exigen login. Lo que mas importa: con la
puerta de resultados cerrada NINGUN valor de resultado (PnL, precio de salida, motivo de cierre,
costos, equity) aparece en el JSON, ni siquiera dentro de los detalles de un rechazo; y la puerta
falla cerrada. App FastAPI minima, nunca la real ni ningun puerto."""

from __future__ import annotations

import ast
import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import serializers
from app.api.routes import account as account_routes
from app.api.routes import arm as arm_routes
from app.api.routes import auth as auth_routes
from app.api.routes import backtest as backtest_routes
from app.api.routes import risk as risk_routes
from app.api.routes import universe as universe_routes
from app.api.security import hash_password
from app.config import Settings
from app.execution.paper_backend import PaperBackend
from app.market.attribution import COINGECKO_ATTRIBUTION
from app.market.universe_diff import UniverseChange, universe_changes
from app.persistence.database import Database
from app.persistence.models import AssetUniverseEntry, BacktestVerdict, Side, Trade
from app.persistence.repositories import (
    backtest_repo,
    risk_rejections_repo,
    system_state_repo,
    trades_repo,
    universe_repo,
)
from app.persistence.repositories import paper_execution_repo as log_repo
from app.trading import control_state, paper_execution, risk_engine
from tests.unit.test_position_monitor import FakeRest

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correcta-123"
H = timedelta(hours=1)


def now() -> datetime:
    return datetime.now(UTC)


def make_settings(**overrides) -> Settings:
    defaults = {
        "AUTH_USERNAME": "renzo", "AUTH_PASSWORD_HASH": hash_password(PASSWORD),
        "JWT_SECRET": "un-secreto-de-prueba-bien-largo-no-el-real", "INITIAL_CAPITAL_USDT": 100.0,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_client(
    db: Database, settings: Settings | None = None, *, backend=None, monitor=None, arm=None,
    scheduler=None, authenticated: bool = True,
) -> TestClient:
    app = FastAPI()
    app.state.db = db
    app.state.settings = settings or make_settings()
    if backend is not None:
        app.state.paper_backend = backend
    if monitor is not None:
        app.state.monitor = monitor
    if arm is not None:
        app.state.paper_execution = arm
    if scheduler is not None:
        app.state.scheduler = scheduler
    for module in (
        auth_routes, account_routes, arm_routes, risk_routes, universe_routes, backtest_routes,
    ):
        app.include_router(module.router)
    client = TestClient(app)
    if authenticated:
        login = client.post("/api/auth/login", json={"username": "renzo", "password": PASSWORD})
        assert login.status_code == 200
    return client


ALL_ROUTES = (
    "/api/account", "/api/arm", "/api/risk", "/api/universe", "/api/backtest/verdicts",
)


@pytest.fixture
def reveal_open(monkeypatch):
    """Simula la puerta abierta (gate de la medicion + registro oficial)."""
    async def allowed(_db):
        return True

    monkeypatch.setattr(account_routes, "reveal_allowed", allowed)
    monkeypatch.setattr(risk_routes, "reveal_allowed", allowed)


@pytest.mark.parametrize("path", ALL_ROUTES)
def test_every_route_requires_a_login(db, path):
    assert make_client(db, authenticated=False).get(path).status_code == 401


# --- cuenta paper --------------------------------------------------------------------------------

# Valores de RESULTADO distintivos: con la puerta cerrada ninguno puede aparecer en ningun JSON.
SENTINELS = (
    "123.456789", "124.567891", "987.654321", "SENTINEL_REASON", "0.013579", "0.024681",
    "0.035791", "777.7777", "666.6666", "SENTINEL_FILL",
)


async def seed_account(db):
    """Una abierta (BTC LONG) y una cerrada (ETH SHORT) con valores centinela de resultado."""
    opened = await trades_repo.create_trade(db, Trade(
        symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21", leverage=10, margin_usdt=5.0,
        notional_usdt=50.0, qty=0.0007, entry_price=64000.5, opened_at=now() - 3 * H,
        sl_price=63100.0, tp_price=66000.0, trailing_distance=2.0, liq_price=58000.0,
        sl_margin_loss_pct=14.0, decision_source="LLM_APROBADA", best_price=777.7777,
        effective_stop=666.6666, fee_entry_usdt=0.013579, funding_paid_usdt=0.024681,
    ))
    closed = await trades_repo.create_trade(db, Trade(
        symbol="ETHUSDT", side=Side.SHORT, strategy="donchian_breakout_20", leverage=10,
        margin_usdt=5.0, notional_usdt=50.0, qty=0.02, entry_price=2500.0,
        opened_at=now() - 10 * H, sl_price=2600.0, tp_price=2300.0, liq_price=2750.0,
        sl_margin_loss_pct=20.0, decision_source="LLM_APROBADA",
    ))
    await trades_repo.close_trade(
        db, closed.id, exit_price=987.654321, fee_exit_usdt=0.013579, pnl_gross_usdt=124.567891,
        pnl_net_usdt=123.456789, close_reason="SENTINEL_REASON", closed_at=now() - 6 * H,
        slippage_exit_usdt=0.035791, fill_source="SENTINEL_FILL",
    )
    return opened, closed


VISIBLE_KEYS = set(serializers._ACCOUNT_TRADE_VISIBLE_FIELDS)
OUTCOME_KEYS = set(serializers._ACCOUNT_TRADE_OUTCOME_FIELDS)


async def test_with_the_gate_closed_no_result_value_appears_anywhere_in_the_response(db):
    await seed_account(db)
    client = make_client(db)

    response = client.get("/api/account")
    body = response.json()

    assert response.status_code == 200
    assert body["reveal_allowed"] is False and body["results"] is None
    assert "ocultos hasta el registro oficial" in body["message"]
    for sentinel in SENTINELS:
        assert sentinel not in response.text, sentinel
    # estructura: exactamente la lista blanca, nada mas (un campo nuevo de Trade no sale solo)
    for trade in (*body["open"], *body["closed"]):
        assert set(trade) == VISIBLE_KEYS


async def test_the_closed_position_shows_its_structure_and_both_hours_without_the_result(db):
    opened, closed = await seed_account(db)

    body = make_client(db).get("/api/account").json()

    assert [t["symbol"] for t in body["open"]] == ["BTCUSDT"]
    assert [t["symbol"] for t in body["closed"]] == ["ETHUSDT"]
    shown = body["closed"][0]
    assert shown["side"] == "SHORT" and shown["strategy"] == "donchian_breakout_20"
    assert (shown["entry_price"], shown["sl_price"], shown["tp_price"]) == (2500.0, 2600.0, 2300.0)
    assert shown["opened_at"] and shown["closed_at"]  # las horas que pediste
    assert shown["status"] == "CLOSED" and shown["decision_source"] == "LLM_APROBADA"
    for hidden in ("exit_price", "close_reason", "pnl_net_usdt", "pnl_gross_usdt", "fill_source"):
        assert hidden not in shown
    assert body["summary"] == {
        "open": 1, "closed": 1, "max_simultaneous_positions": 3, "margin_in_use_usdt": 5.0,
    }
    assert body["initial_capital_usdt"] == 100.0


async def test_with_the_gate_open_the_outcome_fields_and_the_results_summary_appear(
    db, reveal_open,
):
    await seed_account(db)

    body = make_client(db).get("/api/account").json()

    assert body["reveal_allowed"] is True and body["message"] is None
    shown = body["closed"][0]
    assert set(shown) == VISIBLE_KEYS | OUTCOME_KEYS
    assert shown["pnl_net_usdt"] == 123.456789 and shown["exit_price"] == 987.654321
    assert shown["close_reason"] == "SENTINEL_REASON" and shown["fill_source"] == "SENTINEL_FILL"
    results = body["results"]
    assert results["realized_pnl_usdt"] == pytest.approx(123.456789)
    assert results["balance_usdt"] == pytest.approx(223.456789)
    assert (results["closed"], results["wins"], results["losses"]) == (1, 1, 0)
    assert results["profit_factor"] is None  # sin perdidas no hay cociente
    assert results["unrealized_pnl_usdt"] is None  # esta app de prueba no tiene backend


async def test_the_results_summary_computes_pnl_profit_factor_and_equity_with_the_marks(
    db, reveal_open,
):
    settings = make_settings()
    backend = PaperBackend(db, FakeRest(last=100.0), settings)
    opened, _ = await seed_account(db)
    for pnl in (-4.0, 2.0):
        extra = await trades_repo.create_trade(db, Trade(
            symbol="SOLUSDT", side=Side.LONG, strategy="x", leverage=10, margin_usdt=5.0,
            notional_usdt=50.0, qty=1.0, entry_price=100.0, opened_at=now() - 20 * H,
        ))
        await trades_repo.close_trade(
            db, extra.id, exit_price=1.0, fee_exit_usdt=0.0, pnl_gross_usdt=pnl,
            pnl_net_usdt=pnl, close_reason="TP", closed_at=now() - 15 * H,
        )
    monitor = SimpleNamespace(marks={"BTCUSDT": 64500.0})
    client = make_client(db, settings, backend=backend, monitor=monitor)

    results = client.get("/api/account").json()["results"]

    realized = 123.456789 - 4.0 + 2.0
    floating = backend.unrealized_pnl_usdt(opened, 64500.0)
    assert results["realized_pnl_usdt"] == pytest.approx(realized)
    assert results["balance_usdt"] == pytest.approx(100.0 + realized)
    assert results["unrealized_pnl_usdt"] == pytest.approx(floating)
    assert results["equity_usdt"] == pytest.approx(100.0 + realized + floating)
    assert (results["closed"], results["wins"], results["losses"]) == (3, 2, 1)
    assert results["profit_factor"] == pytest.approx((123.456789 + 2.0) / 4.0)
    assert results["marks_missing"] == []
    assert results["balance_usdt"] == pytest.approx(await backend.get_balance())


async def test_a_position_without_a_mark_is_reported_instead_of_guessing_its_pnl(
    db, reveal_open,
):
    settings = make_settings()
    backend = PaperBackend(db, FakeRest(last=100.0), settings)
    await seed_account(db)
    client = make_client(db, settings, backend=backend, monitor=SimpleNamespace(marks={}))

    results = client.get("/api/account").json()["results"]

    assert results["marks_missing"] == ["BTCUSDT"]
    assert results["unrealized_pnl_usdt"] == 0.0  # lo que si se pudo valorar: nada


async def test_the_query_that_reads_pnl_never_runs_while_the_gate_is_closed(db, monkeypatch):
    await seed_account(db)

    async def boom(*args, **kwargs):
        raise AssertionError("se leyo el PnL con la puerta cerrada")

    monkeypatch.setattr(account_routes, "results_summary", boom)

    assert make_client(db).get("/api/account").status_code == 200


async def test_a_broken_gate_fails_closed_end_to_end(db, monkeypatch):
    """Con la funcion REAL de la puerta y la medicion ilegible, nada de resultados sale."""
    from app.trading import reveal

    await seed_account(db)

    async def broken(_db):
        raise RuntimeError("base ilegible")

    monkeypatch.setattr(reveal, "compute_status_async", broken)
    monkeypatch.setattr(reveal, "is_outcome_reveal_allowed", lambda status: True)

    response = make_client(db).get("/api/account")

    assert response.json()["reveal_allowed"] is False
    for sentinel in SENTINELS:
        assert sentinel not in response.text


async def test_the_closed_list_is_newest_first_and_the_limit_is_validated(db):
    for n in range(4):
        trade = await trades_repo.create_trade(db, Trade(
            symbol=f"S{n}USDT", side=Side.LONG, strategy="x", leverage=10, margin_usdt=5.0,
            notional_usdt=50.0, qty=1.0, entry_price=1.0, opened_at=now() - 30 * H,
        ))
        await trades_repo.close_trade(
            db, trade.id, exit_price=1.0, fee_exit_usdt=0.0, pnl_gross_usdt=0.0,
            pnl_net_usdt=0.0, close_reason="TP", closed_at=now() - (10 - n) * H,
        )
    client = make_client(db)

    body = client.get("/api/account?limit=2").json()

    assert [t["symbol"] for t in body["closed"]] == ["S3USDT", "S2USDT"]
    assert body["summary"]["closed"] == 4  # el conteo total, no el de la pagina
    assert client.get("/api/account?limit=0").status_code == 422
    assert client.get("/api/account?limit=501").status_code == 422


# --- registro del brazo ejecutado ----------------------------------------------------------------


async def claim(db, key, **kw):
    fields = dict(
        signal_group_key=key, shadow_trade_id=9, symbol="BTCUSDT", side="LONG",
        strategy="ema_cross_9_21", decision_delay_s=21.0, created_at=now(),
    )
    fields.update(kw)
    assert await log_repo.claim(db, **fields)


async def test_the_arm_log_shows_opened_and_skipped_with_reasons_and_counts(db):
    await claim(db, "a")
    await log_repo.resolve_opened(
        db, "a", trade_id=3, details={"entry_delay_s": 24.5, "adverse_gap_bps": 1.2, "qty": 0.0007},
    )
    await claim(db, "b", symbol="TRXUSDT")
    await log_repo.resolve_skipped(
        db, "b", reason="BELOW_MIN_ORDER", details={"qty": 148.9, "min_trade_volume": 150.0},
    )
    await claim(db, "c")
    await log_repo.resolve_skipped(db, "c", reason="RISK_STRATEGY_NOT_ELIGIBLE")
    await claim(db, "d")  # sigue PENDING

    body = make_client(db).get("/api/arm").json()

    assert body["totals"] == {"opened": 1, "skipped": 2, "pending": 1}
    assert body["skipped_by_reason"] == {"BELOW_MIN_ORDER": 1, "RISK_STRATEGY_NOT_ELIGIBLE": 1}
    assert [e["outcome"] for e in body["entries"]] == ["PENDING", "SKIPPED", "SKIPPED", "OPENED"]
    opened = body["entries"][-1]
    assert opened["trade_id"] == 3 and opened["details"]["adverse_gap_bps"] == 1.2
    below = body["entries"][2]
    assert below["reason"] == "BELOW_MIN_ORDER" and below["details"]["min_trade_volume"] == 150.0
    assert body["config"]["margin_usdt"] == 5.0 and body["config"]["max_decision_delay_s"] == 60.0
    assert "ema_cross_9_21" in body["config"]["eligible_strategies"]


async def test_arm_details_pass_a_whitelist_so_the_equity_never_leaks(db):
    """`MARGIN_UNAVAILABLE` guarda el equity (derivado de resultados); no debe salir."""
    await claim(db, "x")
    await log_repo.resolve_skipped(
        db, "x", reason="RISK_MARGIN_UNAVAILABLE",
        details={"committed_total": 5.0, "equity": 98.7654321, "pnl_net_usdt": -3.14159,
                 "anidado": {"equity": 55.5}, "lista": [1, 2]},
    )

    response = make_client(db).get("/api/arm")

    entry = response.json()["entries"][0]
    assert entry["details"] == {"committed_total": 5.0}
    for leaked in ("98.7654321", "-3.14159", "55.5", "equity", "pnl_net_usdt", "anidado"):
        assert leaked not in response.text, leaked


def test_the_detail_whitelist_drops_nested_structures_and_truncates_text():
    details = serializers.safe_arm_details({
        "message": "x" * 500, "eligible": ["a", "b", 3], "open": 2, "limit": 3, "symbol": None,
        "signal_group_key": "BTCUSDT|LONG|2026", "executed_entry_price": {"a": 1},
    })

    assert details["message"] == "x" * 200 and details["open"] == 2 and details["symbol"] is None
    assert details["eligible"] is None  # una lista con algo que no es texto se descarta
    assert details["executed_entry_price"] is None
    assert "signal_group_key" not in details
    assert serializers.safe_arm_details(None) == {} and serializers.safe_arm_details([1]) == {}


async def test_the_arm_status_comes_from_the_arm_or_says_it_is_off(db):
    off = make_client(db).get("/api/arm").json()["status"]

    class FakeArm:
        async def health_block(self):
            return {"enabled": True, "active": True, "armed_since": "2026-10-17T23:12:13+00:00",
                    "opened_today": 1, "skipped_today": 4}

    on = make_client(db, arm=FakeArm()).get("/api/arm").json()["status"]

    assert off["enabled"] is False and off["active"] is False
    assert on["active"] is True and on["opened_today"] == 1 and on["skipped_today"] == 4


def test_every_skip_reason_the_system_can_write_has_a_legend():
    arm_reasons = {
        value for name, value in vars(paper_execution).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    source = inspect.getsource(risk_engine.check_new_entry)
    risk_codes = set(
        re.findall(r'_reject\(\s*db,\s*symbol,\s*side,\s*strategy,\s*"([A-Z_]+)"', source)
    )

    assert arm_reasons and len(risk_codes) >= 9
    missing = [r for r in arm_reasons if r not in arm_routes.REASON_LEGEND]
    missing += [
        f"{paper_execution.RISK_PREFIX}{c}" for c in risk_codes
        if f"{paper_execution.RISK_PREFIX}{c}" not in arm_routes.REASON_LEGEND
    ]
    assert missing == []


async def test_the_arm_limit_is_validated(db):
    client = make_client(db)

    assert client.get("/api/arm?limit=0").status_code == 422
    assert client.get("/api/arm?limit=201").status_code == 422
    assert client.get("/api/arm?limit=5").status_code == 200


# --- estado de riesgo ----------------------------------------------------------------------------


async def test_a_quiet_system_reports_nothing_blocked(db):
    body = make_client(db).get("/api/risk").json()

    assert body["paused"] is False and body["kill_switch_active"] is False
    assert body["measurement_stopped"] is False and body["entries_blocked_by"] == []
    assert body["circuit_breaker"]["active"] is False and body["circuit_breaker"]["until"] is None
    assert body["drawdown_stop"]["active"] is False and body["drawdown_stop"]["mode"] == "duro"
    assert body["daily_loss"]["locked"] is False
    assert body["positions"]["open"] == 0 and body["positions"]["max_same_direction"] == 2
    assert body["numbers"] is None and "ocultos" in body["message"]
    assert body["rejections"] == [] and body["rejections_today_by_reason"] == {}


async def test_every_blocking_state_is_reported(db):
    await risk_engine.activate_kill_switch(db)
    await control_state.set_scheduler_paused(db, True)
    until = (now() + 5 * H).isoformat()
    await system_state_repo.set_state(db, "circuit_breaker_until", until)
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")
    await system_state_repo.set_state(db, "daily_loss_limit_locked", "1")

    body = make_client(db).get("/api/risk").json()

    assert body["paused"] is True and body["measurement_stopped"] is True
    assert body["kill_switch_active"] is True
    assert body["circuit_breaker"]["active"] is True and body["circuit_breaker"]["until"] == until
    assert body["drawdown_stop"]["active"] is True and body["daily_loss"]["locked"] is True
    assert body["entries_blocked_by"] == [
        "SCHEDULER_PAUSED", "KILL_SWITCH_ACTIVE", "DRAWDOWN_STOP_ACTIVE",
        "CIRCUIT_BREAKER_ACTIVE", "DAILY_LOSS_LIMIT",
    ]


async def test_the_drawdown_stop_in_alert_mode_does_not_block_entries(db):
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")
    client = make_client(db, make_settings(DRAWDOWN_STOP_MODE="alerta"))

    body = client.get("/api/risk").json()

    assert body["drawdown_stop"] == {
        "active": True, "mode": "alerta", "max_drawdown_fraction": 0.2,
    }
    assert "DRAWDOWN_STOP_ACTIVE" not in body["entries_blocked_by"]


async def test_an_expired_breaker_is_not_reported_as_active(db):
    await system_state_repo.set_state(db, "circuit_breaker_until", (now() - H).isoformat())

    body = make_client(db).get("/api/risk").json()

    assert body["circuit_breaker"]["active"] is False and body["entries_blocked_by"] == []


async def test_result_derived_numbers_appear_only_with_the_gate_open(db, reveal_open):
    await system_state_repo.set_state(db, "consecutive_losses", "3")
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "2")

    body = make_client(db).get("/api/risk").json()

    assert body["numbers"] == {"consecutive_losses": 3, "drawdown_trigger_count": 2}
    assert body["message"] is None


async def test_with_the_gate_closed_the_streak_and_the_trigger_count_do_not_leave(db):
    await system_state_repo.set_state(db, "consecutive_losses", "3")
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "27182")

    response = make_client(db).get("/api/risk")

    assert response.json()["numbers"] is None
    assert "27182" not in response.text


async def test_positions_are_counted_by_direction(db):
    for symbol, side in (("BTCUSDT", Side.LONG), ("ETHUSDT", Side.LONG), ("SOLUSDT", Side.SHORT)):
        await trades_repo.create_trade(db, Trade(
            symbol=symbol, side=side, strategy="x", leverage=10, margin_usdt=5.0,
            notional_usdt=50.0, qty=1.0, entry_price=1.0, opened_at=now(),
        ))

    positions = make_client(db).get("/api/risk").json()["positions"]

    assert (positions["open"], positions["long"], positions["short"]) == (3, 2, 1)


async def test_rejections_never_carry_their_details_and_are_counted_by_reason(db):
    await risk_rejections_repo.insert_rejection(
        db, "BTCUSDT", Side.LONG, "ema_cross_9_21", "MARGIN_UNAVAILABLE",
        {"committed_total": 5.0, "equity": 98.7654321},
    )
    await risk_rejections_repo.insert_rejection(
        db, "ETHUSDT", Side.SHORT, "donchian_breakout_20", "STRATEGY_NOT_ELIGIBLE",
        {"eligible": ["ema_cross_9_21"]},
    )
    await risk_rejections_repo.insert_rejection(
        db, "SOLUSDT", Side.LONG, "ema_cross_9_21", "STRATEGY_NOT_ELIGIBLE", {},
    )

    response = make_client(db).get("/api/risk")
    body = response.json()

    assert [r["symbol"] for r in body["rejections"]] == ["SOLUSDT", "ETHUSDT", "BTCUSDT"]
    assert set(body["rejections"][0]) == {
        "id", "created_at", "symbol", "side", "strategy", "reason",
    }
    assert body["rejections_today_by_reason"] == {
        "STRATEGY_NOT_ELIGIBLE": 2, "MARGIN_UNAVAILABLE": 1,
    }
    assert "98.7654321" not in response.text and "details" not in response.text


@pytest.mark.parametrize(("age_min", "stale"), [(5, False), (89, False), (91, True), (None, True)])
async def test_the_scheduler_is_stale_after_ninety_minutes_or_without_a_cycle(db, age_min, stale):
    last = None if age_min is None else now() - timedelta(minutes=age_min)
    client = make_client(db, scheduler=SimpleNamespace(last_cycle_at=last))

    scheduler = client.get("/api/risk").json()["scheduler"]

    assert scheduler["stale"] is stale and scheduler["stale_after_minutes"] == 90
    assert (scheduler["last_cycle_at"] is None) == (last is None)


# --- universo vigente ----------------------------------------------------------------------------


def entry(stamp, symbol, rank, *, included, **kw) -> AssetUniverseEntry:
    values = dict(
        refreshed_at=stamp, coingecko_id=symbol.removesuffix("USDT").lower(), symbol=symbol,
        coingecko_rank=rank, market_cap_usd=1e9 * (50 - rank), has_bitunix_perp=True,
        price_sanity_ok=True, included=included,
    )
    values.update(kw)
    return AssetUniverseEntry(**values)


async def seed_universe(db, *, last_age_hours=2.0):
    t3 = now() - timedelta(hours=last_age_hours)
    t2, t1 = t3 - 24 * H, t3 - 48 * H
    await universe_repo.insert_snapshot(db, [
        entry(t1, "BTCUSDT", 1, included=True), entry(t1, "XMRUSDT", 2, included=True),
        entry(t1, "LINKUSDT", 3, included=False),
    ])
    await universe_repo.insert_snapshot(db, [
        entry(t2, "BTCUSDT", 1, included=True), entry(t2, "XMRUSDT", 2, included=False),
        entry(t2, "LINKUSDT", 3, included=True),
    ])
    await universe_repo.insert_snapshot(db, [
        entry(t3, "BTCUSDT", 1, included=True), entry(t3, "LINKUSDT", 2, included=True),
        entry(t3, "USDTUSDT", 3, included=False, excluded_category="stablecoins",
              has_bitunix_perp=False, price_sanity_ok=None),
        entry(t3, "FIGRUSDT", 4, included=False, has_bitunix_perp=False, price_sanity_ok=None),
        entry(t3, "XMRUSDT", 5, included=False),
        entry(t3, "ODDUSDT", 6, included=False, price_sanity_ok=False),
        entry(t3, "MANUSDT", 7, included=False, excluded_manual=True),
    ])


async def test_the_universe_shows_the_current_snapshot_with_each_exclusion_reason(db):
    await seed_universe(db)
    monitor = SimpleNamespace(marks={"BTCUSDT": 64000.5, "XMRUSDT": 123.0})

    body = make_client(db, monitor=monitor).get("/api/universe").json()

    assert body["included"] == ["BTCUSDT", "LINKUSDT"]
    by_symbol = {c["symbol"]: c for c in body["candidates"]}
    assert [c["rank"] for c in body["candidates"]] == [1, 2, 3, 4, 5, 6, 7]
    assert by_symbol["BTCUSDT"]["mark_price"] == 64000.5 and by_symbol["BTCUSDT"]["included"]
    assert by_symbol["LINKUSDT"]["mark_price"] is None  # incluido pero el monitor aun no tiene mark
    assert by_symbol["XMRUSDT"]["mark_price"] is None  # no incluido: no se muestra mark
    assert by_symbol["USDTUSDT"]["exclusion_reason"] == "Categoria excluida: stablecoins"
    assert by_symbol["FIGRUSDT"]["exclusion_reason"] == "Sin perpetuo USDT activo en Bitunix"
    assert by_symbol["XMRUSDT"]["exclusion_reason"].startswith("Fuera del tamano del universo")
    assert by_symbol["ODDUSDT"]["exclusion_reason"].startswith("Fallo la verificacion de precio")
    assert by_symbol["MANUSDT"]["exclusion_reason"].startswith("Excluida manualmente")
    assert by_symbol["BTCUSDT"]["exclusion_reason"] is None


async def test_the_universe_lists_the_recent_changes_newest_first(db):
    await seed_universe(db)

    body = make_client(db).get("/api/universe").json()

    # t1 -> t2 cambia (entra LINK, sale XMR); t2 -> t3 no cambia el conjunto de incluidos
    assert len(body["changes"]) == 1
    assert body["changes"][0]["added"] == ["LINKUSDT"]
    assert body["changes"][0]["removed"] == ["XMRUSDT"]


async def test_the_universe_carries_the_coingecko_attribution_and_the_rule(db):
    await seed_universe(db)

    body = make_client(db).get("/api/universe").json()

    assert body["attribution"] == COINGECKO_ATTRIBUTION == {
        "text": "Data provided by CoinGecko", "url": "https://www.coingecko.com",
    }
    assert "NO se cierra por la fuerza" in body["rule"]


@pytest.mark.parametrize(("age_hours", "stale"), [(2.0, False), (47.0, False), (49.0, True)])
async def test_the_universe_is_stale_past_the_configured_limit(db, age_hours, stale):
    await seed_universe(db, last_age_hours=age_hours)

    body = make_client(db).get("/api/universe").json()

    assert body["stale"] is stale and body["staleness_limit_hours"] == 48.0
    assert body["age_hours"] == pytest.approx(age_hours, abs=0.1)


async def test_the_last_refresh_error_is_shown_only_when_there_is_one(db):
    await seed_universe(db)
    client = make_client(db)
    await system_state_repo.set_state(db, "universe_last_refresh_error", "")
    assert client.get("/api/universe").json()["last_refresh_error"] is None

    await system_state_repo.set_state(db, "universe_last_refresh_error", "2026 CoinGeckoApiError")

    assert client.get("/api/universe").json()["last_refresh_error"] == "2026 CoinGeckoApiError"


async def test_an_empty_database_gives_an_empty_stale_universe(db):
    body = make_client(db).get("/api/universe").json()

    assert body["candidates"] == [] and body["included"] == [] and body["changes"] == []
    assert body["stale"] is True and body["refreshed_at"] is None and body["age_hours"] is None
    assert body["attribution"]["text"] == "Data provided by CoinGecko"


def test_universe_changes_between_consecutive_snapshots_only():
    t = datetime(2026, 11, 1, tzinfo=UTC)
    history = [
        (t, frozenset({"A", "B"})),
        (t + H, frozenset({"A", "B"})),            # sin cambio
        (t + 2 * H, frozenset({"A", "C"})),        # +C -B
        (t + 3 * H, frozenset({"A", "C", "D"})),   # +D
    ]

    changes = universe_changes(history)

    assert changes == [
        UniverseChange(at=t + 3 * H, added=("D",), removed=()),
        UniverseChange(at=t + 2 * H, added=("C",), removed=("B",)),
    ]
    assert universe_changes([]) == [] and universe_changes(history[:1]) == []


async def test_the_included_history_is_oldest_first_and_keeps_empty_snapshots(db):
    t1, t2 = now() - 48 * H, now() - 24 * H
    await universe_repo.insert_snapshot(db, [entry(t1, "BTCUSDT", 1, included=True)])
    await universe_repo.insert_snapshot(db, [entry(t2, "BTCUSDT", 1, included=False)])

    history = await universe_repo.get_included_history(db, limit_snapshots=10)

    assert [(stamp, symbols) for stamp, symbols in history] == [
        (t1, frozenset({"BTCUSDT"})), (t2, frozenset()),
    ]
    assert await universe_repo.get_included_history(db, limit_snapshots=1) == [
        (t2, frozenset()),
    ]


# --- veredictos de backtest ----------------------------------------------------------------------


def verdict(strategy, **kw) -> BacktestVerdict:
    values = dict(
        strategy=strategy, combos_tested=10, total_trades_all_segments=500,
        pf_oos_aggregate=1.1233661231946566, pct_symbols_pf_gt1=50.0, pct_folds_positive=70.37,
        pf_stressed=1.05, max_drawdown_oos_pct=85.04, concentration_pct=65.73,
        run_at=datetime(2026, 10, 1, 23, 5, tzinfo=UTC),
    )
    values.update(kw)
    return BacktestVerdict(**values)


async def test_the_verdicts_show_reasons_with_rounded_numbers_and_the_eligibility(db):
    await backtest_repo.insert_verdict(db, verdict(
        "ema_cross_9_21", discarded=True,
        discard_reasons_json='["PF OOS agregado 1.1233661231946566 < minimo 1.2", '
                             '"Drawdown OOS 85.0% > 50.0%"]',
    ))
    await backtest_repo.insert_verdict(db, verdict("donchian_breakout_20", discarded=True))
    await backtest_repo.insert_verdict(db, verdict(
        "funding_contrarian_experimental", is_experimental=True,
    ))

    body = make_client(db).get("/api/backtest/verdicts").json()

    by_strategy = {v["strategy"]: v for v in body["verdicts"]}
    assert by_strategy["ema_cross_9_21"]["reasons"] == [
        "PF OOS agregado 1.12 < minimo 1.2", "Drawdown OOS 85.0% > 50.0%",
    ]
    assert by_strategy["ema_cross_9_21"]["discarded"] is True
    assert by_strategy["ema_cross_9_21"]["eligible_for_account"] is True  # esta en la lista
    assert by_strategy["donchian_breakout_20"]["eligible_for_account"] is False
    assert by_strategy["funding_contrarian_experimental"]["experimental"] is True
    assert by_strategy["funding_contrarian_experimental"]["reasons"] == []
    assert body["all_strategies_eligible"] is False
    assert body["run_at"].startswith("2026-10-01T23:05")
    assert "no la medicion en vivo" in body["note"]


async def test_an_empty_eligibility_list_means_every_strategy_is_eligible(db):
    await backtest_repo.insert_verdict(db, verdict("donchian_breakout_20", discarded=True))
    client = make_client(db, make_settings(REAL_ACCOUNT_ELIGIBLE_STRATEGIES=""))

    body = client.get("/api/backtest/verdicts").json()

    assert body["all_strategies_eligible"] is True and body["eligible_strategies"] == []
    assert body["verdicts"][0]["eligible_for_account"] is True


async def test_no_verdicts_is_an_empty_list_not_an_error(db):
    body = make_client(db).get("/api/backtest/verdicts").json()

    assert body["verdicts"] == [] and body["run_at"] is None


@pytest.mark.parametrize(("raw", "expected"), [
    (None, []), ("", []), ("no es json", []), ('["a", 3, "b 2.123456 c"]', ["a", "b 2.12 c"]),
    ("{}", []), ('{"a": "b"}', []), ('"suelto"', []), ("null", []),
])
def test_reasons_parsing_never_raises(raw, expected):
    assert backtest_routes.parse_reasons(raw) == expected


# --- estructura: solo lectura, sin tocar el nucleo -----------------------------------------------

ROUTE_FILES = ("account.py", "arm.py", "risk.py", "universe.py", "backtest.py")


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


@pytest.mark.parametrize("name", ROUTE_FILES)
def test_the_read_routes_never_import_the_llm_the_labeler_or_an_execution_backend(name):
    # `app.llm.budget` solo aporta `local_day_start_utc` (fechas): tambien lo usan /health y
    # Telegram.
    forbidden = (
        "anthropic", "app.llm.prompts", "app.llm.prompts_v5", "app.llm.client",
        "app.llm.decision_service", "app.llm.market_features", "app.llm._anthropic_client",
        "app.trading.shadow_live_labeler", "app.trading.shadow_book", "app.execution",
        "app.news", "app.memory",
    )
    imported = _imports(REPO_ROOT / "app" / "api" / "routes" / name)

    assert [m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)] == []


@pytest.mark.parametrize("name", ROUTE_FILES)
def test_the_read_routes_contain_no_write_statements(name):
    source = (REPO_ROOT / "app" / "api" / "routes" / name).read_text(encoding="utf-8")

    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER)\b", source)
    assert "@router.post" not in source and "@router.put" not in source
    assert "@router.delete" not in source
