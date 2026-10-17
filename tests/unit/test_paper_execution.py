"""Brazo ejecutado de la cuenta PAPER (subfase 3.9): `app.trading.paper_execution`.

Cada regla, cada codigo de rechazo del motor de riesgo, los bordes de 60 s y de tamano minimo,
la idempotencia, la resolucion de filas PENDING y la activacion. `PaperBackend` y el motor de
riesgo son los REALES (base en memoria); solo el cliente REST es falso. Sin red ni gasto."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.execution.paper_backend import PaperBackend
from app.llm.decision_service import LlmDecisionResult
from app.persistence.models import ContractSpec, ShadowTrade, Side, TradeStatus
from app.persistence.repositories import (
    health_repo,
    risk_rejections_repo,
    specs_repo,
    system_state_repo,
    trades_repo,
)
from app.persistence.repositories import paper_execution_repo as log_repo
from app.trading import control_state, paper_execution, risk_engine
from app.trading.levels import StrategyLevels
from app.trading.paper_execution import (
    DECISION_SOURCE,
    KEY_REAL_ARM_DRAWDOWN_BASELINE,
    KEY_REAL_ARM_STARTED_AT,
    PaperExecutionArm,
    adverse_gap_bps,
    check_order_size,
    floor_to_precision,
)
from app.trading.signal_generator import SignalCandidate

NOW = datetime.now(UTC)
ELIGIBLE = "ema_cross_9_21"


class FakeRestClient:
    """Sin red: precio fijo controlable (opcionalmente falla)."""

    def __init__(self, price: float = 100.0) -> None:
        self.price = price
        self.fail = False

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        if self.fail:
            raise RuntimeError("REST caido")
        return [{"symbol": symbol, "lastPrice": str(self.price), "markPrice": str(self.price)}]


def make_settings(**overrides) -> Settings:
    defaults = dict(
        PAPER_EXECUTION_ENABLED=True, LLM_LIVE_LABELING_ENABLED=True, DEFAULT_MARGIN_USDT=5.0,
        LEVERAGE=10, INITIAL_CAPITAL_USDT=100.0, MAX_SIMULTANEOUS_POSITIONS=3,
        MAX_SAME_DIRECTION_POSITIONS=2,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_arm(db, price=100.0, **settings_overrides):
    settings = make_settings(**settings_overrides)
    rest = FakeRestClient(price)
    backend = PaperBackend(db, rest, settings)
    arm = PaperExecutionArm(db, settings, backend)
    arm.active = True
    return arm, backend, rest, settings


async def add_spec(db, symbol="BTCUSDT", min_volume=0.001, precision=3):
    await specs_repo.upsert_spec(db, ContractSpec(
        symbol=symbol, min_trade_volume=min_volume, base_precision=precision, fetched_at=NOW,
    ))


def make_shadow(symbol="BTCUSDT", side=Side.LONG, key="BTCUSDT|LONG|c1", price=100.0, id_=1):
    return ShadowTrade(
        id=id_, symbol=symbol, side=side, strategy=ELIGIBLE, status=TradeStatus.OPEN,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=0.5, entry_price=price,
        opened_at=NOW, contributing_strategies=[ELIGIBLE], signal_group_key=key,
        candle_close_time=NOW,
    )


def make_candidate(
    symbol="BTCUSDT", side=Side.LONG, strategies=(ELIGIBLE,), stop=97.0, take_profit=110.0,
    sl_pct=30.0, close_age_s=30.0,
):
    levels = StrategyLevels(stop_price=stop, take_profit_price=take_profit)
    close = datetime.now(UTC) - timedelta(seconds=close_age_s)
    return SignalCandidate(
        symbol=symbol, side=side, candle_close_time=close,
        contributing_strategies=list(strategies), sl_margin_loss_pct=sl_pct,
        levels_by_strategy={s: levels for s in strategies},
        price_by_strategy={s: 100.0 for s in strategies},
    )


def make_result(label="APROBADA", delay=25.0) -> LlmDecisionResult:
    return LlmDecisionResult("OK", label, delay, 1)


async def run(arm, db, *, shadow=None, result=None, candidate=None, spec=True):
    if spec:
        await add_spec(db)
    return await arm.handle(
        shadow or make_shadow(), result or make_result(), candidate or make_candidate(),
    )


# --- funciones puras ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("qty", "precision", "expected"),
    [
        (0.000641, 4, 0.0006), (149.03, 0, 149.0), (0.3, 1, 0.3), (0.29999999999, 1, 0.3),
        (1.0, 0, 1.0), (0.0099, 2, 0.0), (5.55, None, 5.55), (123.456, 2, 123.45),
    ],
)
def test_floor_to_precision_rounds_down_and_tolerates_float_noise(qty, precision, expected):
    assert floor_to_precision(qty, precision) == pytest.approx(expected, abs=1e-12)


def test_order_size_check_uses_the_floored_qty_against_the_minimum():
    spec = ContractSpec(symbol="TRXUSDT", min_trade_volume=150.0, base_precision=0, fetched_at=NOW)

    common = dict(margin_usdt=5.0, leverage=10, taker_fee_pct=0.0006, spec=spec)
    trx = check_order_size(price=0.3355, **common)
    exact = check_order_size(price=0.3333, **common)

    assert trx.qty == pytest.approx(149.03, abs=0.01) and trx.qty_floored == 149.0
    assert trx.executable is False
    assert exact.qty_floored == 150.0 and exact.executable is True  # 150 justo: pasa


def test_order_size_check_without_a_known_minimum_does_not_block():
    spec = ContractSpec(symbol="XUSDT", min_trade_volume=None, base_precision=2, fetched_at=NOW)

    assert check_order_size(
        margin_usdt=5.0, leverage=10, price=100.0, taker_fee_pct=0.0006, spec=spec
    ).executable


def test_adverse_gap_is_positive_when_the_fill_is_worse_for_the_position():
    assert adverse_gap_bps(Side.LONG, 100.0, 100.1) == pytest.approx(10.0)  # pago mas
    assert adverse_gap_bps(Side.SHORT, 100.0, 99.9) == pytest.approx(10.0)  # vendo mas barato
    assert adverse_gap_bps(Side.LONG, 100.0, 99.9) == pytest.approx(-10.0)  # mejor que la sombra
    assert adverse_gap_bps(Side.LONG, 0.0, 100.0) is None


# --- apertura y registro ------------------------------------------------------------------


async def test_an_approved_on_time_decision_opens_a_paper_trade_marked_llm_aprobada(db):
    arm, _, rest, _ = make_arm(db)
    rest.price = 100.1  # la ejecucion llega 10 pb peor que la sombra

    outcome = await run(arm, db)

    assert outcome == "OPENED"
    trades = await trades_repo.get_open_positions(db)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.decision_source == DECISION_SOURCE == "LLM_APROBADA"
    assert trade.strategy == ELIGIBLE and trade.margin_usdt == 5.0 and trade.leverage == 10
    assert trade.sl_price == 97.0 and trade.tp_price == 110.0
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.outcome == "OPENED" and entry.reason is None and entry.trade_id == trade.id
    assert entry.shadow_trade_id == 1 and entry.strategy == ELIGIBLE
    assert entry.details["shadow_entry_price"] == 100.0
    assert entry.details["executed_entry_price"] == 100.1
    assert entry.details["adverse_gap_bps"] == pytest.approx(10.0)
    assert entry.details["entry_delay_s"] == pytest.approx(30.0, abs=5.0)


async def test_a_short_is_opened_and_its_gap_is_signed_for_the_short(db):
    arm, _, rest, _ = make_arm(db)
    rest.price = 99.9
    candidate = make_candidate(side=Side.SHORT, stop=103.0, take_profit=90.0)

    outcome = await run(
        arm, db, shadow=make_shadow(side=Side.SHORT, key="BTCUSDT|SHORT|c1"), candidate=candidate,
    )

    assert outcome == "OPENED"
    entry = await log_repo.get_by_key(db, "BTCUSDT|SHORT|c1")
    assert entry.side == "SHORT" and entry.details["adverse_gap_bps"] == pytest.approx(10.0)


@pytest.mark.parametrize("label", ["RECHAZADA", "SIN_LLM"])
async def test_only_approved_decisions_belong_to_the_arm(db, label):
    arm, _, _, _ = make_arm(db)

    outcome = await run(arm, db, result=make_result(label=label))

    assert outcome is None
    assert await log_repo.get_recent(db) == []  # ni siquiera una fila
    assert await trades_repo.get_open_positions(db) == []


async def test_the_same_group_is_never_opened_twice(db):
    arm, _, _, _ = make_arm(db)

    first = await run(arm, db)
    second = await run(arm, db)

    assert first == "OPENED" and second is None
    assert len(await trades_repo.get_trades(db)) == 1
    assert len(await log_repo.get_recent(db)) == 1


@pytest.mark.parametrize(
    ("delay", "opens"), [(60.0, True), (60.01, False), (74.1, False), (5.0, True)],
)
async def test_the_decision_must_arrive_within_the_real_delay_limit(db, delay, opens):
    arm, _, _, _ = make_arm(db)

    outcome = await run(arm, db, result=make_result(delay=delay))

    if opens:
        assert outcome == "OPENED"
    else:
        assert outcome == "LATE_DECISION"
        entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
        assert entry.outcome == "SKIPPED" and entry.details == {
            "decision_delay_s": delay, "limit_s": 60.0,
        }
        assert await trades_repo.get_open_positions(db) == []


async def test_the_delay_limit_follows_the_setting(db):
    arm, _, _, _ = make_arm(db, LLM_REAL_MAX_DELAY_SECONDS=30.0)

    assert await run(arm, db, result=make_result(delay=31.0)) == "LATE_DECISION"


async def test_a_paused_scheduler_opens_nothing(db):
    arm, _, _, _ = make_arm(db)
    await control_state.set_scheduler_paused(db, True)

    outcome = await run(arm, db)

    assert outcome == "SCHEDULER_PAUSED"
    assert await trades_repo.get_open_positions(db) == []


async def test_a_symbol_with_an_open_position_is_skipped(db):
    arm, backend, _, _ = make_arm(db)
    await add_spec(db)
    await backend.open_position("BTCUSDT", Side.SHORT, 5.0, 10, is_manual=True)

    outcome = await run(arm, db)

    assert outcome == "POSITION_ALREADY_OPEN"
    assert len(await trades_repo.get_open_positions(db)) == 1


# --- tamano minimo ------------------------------------------------------------------------


async def test_without_a_cached_contract_spec_it_is_not_opened(db):
    arm, _, _, _ = make_arm(db)

    outcome = await run(arm, db, spec=False)

    assert outcome == "NO_CONTRACT_SPEC"


async def test_trx_with_five_usdt_at_10x_is_below_the_minimum_and_the_reason_is_queryable(db):
    arm, _, rest, _ = make_arm(db, price=0.3355)
    await add_spec(db, symbol="TRXUSDT", min_volume=150.0, precision=0)
    shadow = make_shadow(symbol="TRXUSDT", key="TRXUSDT|LONG|c1", price=0.3355)
    candidate = make_candidate(symbol="TRXUSDT", stop=0.3, take_profit=0.4, sl_pct=30.0)

    outcome = await arm.handle(shadow, make_result(), candidate)

    assert outcome == "BELOW_MIN_ORDER"
    entry = await log_repo.get_by_key(db, "TRXUSDT|LONG|c1")
    assert entry.outcome == "SKIPPED" and entry.reason == "BELOW_MIN_ORDER"
    assert entry.details["qty_floored"] == 149.0 and entry.details["min_trade_volume"] == 150.0
    assert entry.details["price"] == 0.3355 and entry.details["base_precision"] == 0
    assert await log_repo.count_by_reason(db) == {"BELOW_MIN_ORDER": 1}
    assert await trades_repo.get_open_positions(db) == []  # y el margen NO se toco


async def test_exactly_the_minimum_after_flooring_opens(db):
    arm, _, _, _ = make_arm(db, price=50.0)
    await add_spec(db, min_volume=1.0, precision=0)  # 50 USDT / 50 = 1.0 justo el minimo
    shadow = make_shadow(price=50.0)
    candidate = make_candidate(stop=48.5, take_profit=60.0, sl_pct=30.0)

    assert await arm.handle(shadow, make_result(), candidate) == "OPENED"


async def test_the_floor_is_stricter_than_the_paper_backend_check(db):
    """Qty bruta 0,0064 >= minimo 0,0062 (PaperBackend lo dejaria pasar), pero redondeada a
    3 decimales es 0,006 < 0,0062: un exchange real la rechazaria."""
    price = 50.0 / 0.0064
    arm, _, _, _ = make_arm(db, price=price)
    await add_spec(db, min_volume=0.0062, precision=3)
    shadow = make_shadow(price=price)
    candidate = make_candidate(stop=price * 0.97, take_profit=price * 1.1)

    outcome = await arm.handle(shadow, make_result(), candidate)

    assert outcome == "BELOW_MIN_ORDER"
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.details["qty"] == pytest.approx(0.0064) and entry.details["qty_floored"] == 0.006


async def test_a_margin_cut_by_the_risk_engine_that_breaks_the_minimum_is_below_min_order(db):
    # tope por activo del 3 % del capital = 3 USDT de margen: la preevaluacion del brazo (5 USDT
    # x10 / 100 = 0,5 >= 0,4) pasa, pero con el margen recortado (0,3) el PaperBackend rechaza.
    arm, _, _, _ = make_arm(db, MAX_CAPITAL_PCT_PER_ASSET=0.03)
    await add_spec(db, min_volume=0.4, precision=None)

    outcome = await arm.handle(make_shadow(), make_result(), make_candidate())

    assert outcome == "BELOW_MIN_ORDER"
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert "minimo operable" in entry.details["message"]
    assert entry.details["qty"] == pytest.approx(0.5)  # lo que veia la preevaluacion
    assert await trades_repo.get_open_positions(db) == []


# --- motor de riesgo: cada codigo queda como RISK_<codigo> --------------------------------


async def _kill_switch(db, backend):
    await risk_engine.activate_kill_switch(db)


async def _circuit_breaker(db, backend):
    until = datetime.now(UTC) + timedelta(hours=1)
    await system_state_repo.set_state(db, risk_engine._KEY_CIRCUIT_BREAKER_UNTIL, until.isoformat())


async def _drawdown_stop(db, backend):
    await system_state_repo.set_state(db, risk_engine._KEY_DRAWDOWN_STOP_ACTIVE, "1")


async def _daily_loss(db, backend):
    day = risk_engine.today_key("America/Santiago")
    await system_state_repo.set_state(db, risk_engine._KEY_DAILY_LOSS_DAY, day)
    await system_state_repo.set_state(db, risk_engine._KEY_DAILY_LOSS_BASELINE, "100.0")
    await system_state_repo.set_state(db, risk_engine._KEY_DAILY_LOSS_LOCKED, "1")


async def _stale_feed(db, backend):
    await health_repo.record_success(
        db, health_repo.WS_FEED_SOURCE, datetime.now(UTC) - timedelta(hours=1)
    )


async def _max_positions(db, backend):
    for symbol, side in (("ETHUSDT", Side.LONG), ("SOLUSDT", Side.SHORT), ("XRPUSDT", Side.SHORT)):
        await backend.open_position(symbol, side, 5.0, 10, is_manual=True)


async def _max_same_direction(db, backend):
    for symbol in ("ETHUSDT", "SOLUSDT"):
        await backend.open_position(symbol, Side.LONG, 5.0, 10, is_manual=True)


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (_kill_switch, "KILL_SWITCH_ACTIVE"),
        (_circuit_breaker, "CIRCUIT_BREAKER_ACTIVE"),
        (_drawdown_stop, "DRAWDOWN_STOP_ACTIVE"),
        (_daily_loss, "DAILY_LOSS_LIMIT"),
        (_stale_feed, "DATA_STALE"),
        (_max_positions, "MAX_SIMULTANEOUS_POSITIONS"),
        (_max_same_direction, "MAX_SAME_DIRECTION_POSITIONS"),
    ],
)
async def test_each_risk_engine_block_is_recorded_as_a_risk_reason(db, prepare, reason):
    arm, backend, _, _ = make_arm(db)
    await add_spec(db)
    await prepare(db, backend)
    open_before = len(await trades_repo.get_open_positions(db))

    outcome = await arm.handle(make_shadow(), make_result(), make_candidate())

    assert outcome == f"RISK_{reason}"
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.outcome == "SKIPPED" and entry.reason == f"RISK_{reason}"
    assert len(await trades_repo.get_open_positions(db)) == open_before
    rejections = await risk_rejections_repo.get_rejections(db, reason=reason)
    assert rejections and rejections[0].symbol == "BTCUSDT"  # el motor de riesgo tambien lo audita


async def test_a_strategy_outside_the_eligible_list_is_blocked_by_the_risk_engine(db):
    arm, _, _, _ = make_arm(db)
    candidate = make_candidate(strategies=("donchian_breakout_20",))

    outcome = await run(arm, db, candidate=candidate)

    assert outcome == "RISK_STRATEGY_NOT_ELIGIBLE"
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.strategy == "donchian_breakout_20"
    assert entry.details == {"eligible": arm.settings.real_account_eligible_strategies_list}


async def test_a_group_with_an_eligible_contributor_is_attributed_to_it(db):
    arm, _, _, _ = make_arm(db)
    candidate = make_candidate(strategies=("donchian_breakout_20", ELIGIBLE))

    outcome = await run(arm, db, candidate=candidate)

    assert outcome == "OPENED"
    assert (await trades_repo.get_open_positions(db))[0].strategy == ELIGIBLE


async def test_a_stop_loss_above_the_live_cap_is_blocked(db):
    arm, _, _, _ = make_arm(db)

    outcome = await run(arm, db, candidate=make_candidate(sl_pct=60.0))

    assert outcome == "RISK_SL_CAP_EXCEEDED"


async def test_a_planned_stop_already_passed_at_fill_time_is_levels_invalid(db):
    arm, _, rest, _ = make_arm(db)
    rest.price = 96.0  # el mercado ya cruzo el SL planeado (97) de un LONG

    outcome = await run(arm, db)

    assert outcome == "LEVELS_INVALID"
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert "SL planeado" in entry.details["message"]
    assert await trades_repo.get_open_positions(db) == []


async def test_a_failure_while_opening_is_recorded_with_its_type_and_does_not_raise(db):
    arm, _, rest, _ = make_arm(db)
    rest.fail = True

    outcome = await run(arm, db)

    assert outcome == "OPEN_ERROR"
    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.outcome == "SKIPPED" and entry.details == {"error_type": "RuntimeError"}


# --- submit: aislado y sin excepciones ----------------------------------------------------


async def _drain(arm):
    tasks = list(arm._tasks)
    if tasks:
        await asyncio.wait(tasks)


async def test_submit_runs_the_arm_in_its_own_task(db):
    arm, _, _, _ = make_arm(db)
    await add_spec(db)

    arm.submit(make_shadow(), make_result(), make_candidate())
    assert len(arm._tasks) == 1
    await _drain(arm)

    assert (await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")).outcome == "OPENED"
    assert arm._tasks == set()


async def test_submit_does_nothing_when_inactive_or_not_approved_or_without_candidate(db):
    arm, _, _, _ = make_arm(db)

    arm.active = False
    arm.submit(make_shadow(), make_result(), make_candidate())
    arm.active = True
    arm.submit(make_shadow(), make_result(label="RECHAZADA"), make_candidate())
    arm.submit(make_shadow(), None, make_candidate())  # FakeDecisionService devuelve None
    arm.submit(make_shadow(), make_result(), None)

    assert arm._tasks == set()
    assert await log_repo.get_recent(db) == []


async def test_an_unexpected_failure_is_contained_logged_and_leaves_a_reason(
    db, monkeypatch, caplog,
):
    arm, _, _, _ = make_arm(db)
    await add_spec(db)

    async def boom(_db):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(control_state, "is_scheduler_paused", boom)

    with caplog.at_level(logging.ERROR, logger="app.trading.paper_execution"):
        arm.submit(make_shadow(), make_result(), make_candidate())
        await _drain(arm)

    entry = await log_repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.outcome == "SKIPPED" and entry.reason == "OPEN_ERROR"
    assert entry.details == {"error_type": "RuntimeError"}
    assert any("fallo inesperado" in r.getMessage() for r in caplog.records)


async def test_submit_never_raises_even_if_the_task_cannot_be_created(db, monkeypatch):
    arm, _, _, _ = make_arm(db)

    def no_task(coro):
        coro.close()
        raise RuntimeError("sin loop")

    monkeypatch.setattr(paper_execution.asyncio, "create_task", no_task)

    arm.submit(make_shadow(), make_result(), make_candidate())  # no lanza

    assert arm._tasks == set()


async def test_shutdown_cancels_a_hung_task_and_leaves_the_row_pending_for_the_next_start(db):
    arm, _, _, _ = make_arm(db)
    await add_spec(db)
    gate = asyncio.Event()

    async def hung(*args, **kwargs):
        await gate.wait()

    arm.paper_backend.open_position = hung  # type: ignore[method-assign]
    arm.submit(make_shadow(), make_result(), make_candidate())
    for _ in range(50):  # deja que llegue a la apertura colgada
        await asyncio.sleep(0)
        if (await log_repo.get_pending(db)):
            break

    await arm.shutdown()

    assert arm._tasks == set()
    assert [e.signal_group_key for e in await log_repo.get_pending(db)] == ["BTCUSDT|LONG|c1"]


# --- activacion ---------------------------------------------------------------------------


async def test_activate_does_nothing_when_the_switch_is_off(db):
    arm, _, _, _ = make_arm(db, PAPER_EXECUTION_ENABLED=False)
    arm.active = False

    assert await arm.activate() is False
    assert arm.active is False
    assert await system_state_repo.get_state(db, KEY_REAL_ARM_STARTED_AT) is None


async def test_activate_fixes_the_arm_keys_once_and_never_overwrites_them(db):
    arm, _, _, _ = make_arm(db)
    arm.active = False
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "2")

    assert await arm.activate() is True
    started = await system_state_repo.get_state(db, KEY_REAL_ARM_STARTED_AT)
    assert await system_state_repo.get_state(db, KEY_REAL_ARM_DRAWDOWN_BASELINE) == "2"
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "5")
    assert await arm.activate() is True  # segundo arranque

    assert await system_state_repo.get_state(db, KEY_REAL_ARM_STARTED_AT) == started
    assert await system_state_repo.get_state(db, KEY_REAL_ARM_DRAWDOWN_BASELINE) == "2"
    assert arm.active is True


async def test_activate_refuses_when_auto_open_without_llm_is_on(db, caplog):
    arm, _, _, _ = make_arm(db, AUTO_OPEN_WITHOUT_LLM=True)
    arm.active = False

    with caplog.at_level(logging.CRITICAL, logger="app.trading.paper_execution"):
        assert await arm.activate() is False

    assert arm.active is False
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert await system_state_repo.get_state(db, KEY_REAL_ARM_STARTED_AT) is None


async def test_activate_warns_when_nothing_will_ever_label(db, caplog):
    arm, _, _, _ = make_arm(db, LLM_LIVE_LABELING_ENABLED=False)
    arm.active = False

    with caplog.at_level(logging.WARNING, logger="app.trading.paper_execution"):
        assert await arm.activate() is True

    assert any("no abrira nada" in r.getMessage() for r in caplog.records)


async def test_activate_resolves_pending_rows_left_by_a_restart(db):
    arm, backend, _, _ = make_arm(db)
    arm.active = False
    await add_spec(db)
    # a) el proceso murio justo DESPUES de abrir: existe el trade LLM_APROBADA
    created = datetime.now(UTC) - timedelta(seconds=40)
    await log_repo.claim(
        db, signal_group_key="BTCUSDT|LONG|a", shadow_trade_id=1, symbol="BTCUSDT", side="LONG",
        strategy=ELIGIBLE, decision_delay_s=20.0, created_at=created,
    )
    trade = await backend.open_position(
        "BTCUSDT", Side.LONG, 5.0, 10, strategy=ELIGIBLE, sl_margin_loss_pct=30.0,
        decision_source=DECISION_SOURCE, levels=StrategyLevels(97.0, 110.0),
    )
    # b) murio ANTES de abrir: no hay trade
    await log_repo.claim(
        db, signal_group_key="ETHUSDT|LONG|b", shadow_trade_id=2, symbol="ETHUSDT", side="LONG",
        strategy=ELIGIBLE, decision_delay_s=20.0, created_at=created,
    )

    await arm.activate()

    a = await log_repo.get_by_key(db, "BTCUSDT|LONG|a")
    b = await log_repo.get_by_key(db, "ETHUSDT|LONG|b")
    assert a.outcome == "OPENED" and a.trade_id == trade.id
    assert b.outcome == "SKIPPED" and b.reason == "ABORTED_RESTART"
    assert await log_repo.get_pending(db) == []


async def test_health_block_counts_the_day_without_any_pnl(db):
    arm, _, _, _ = make_arm(db)
    await run(arm, db)  # OPENED
    await arm.handle(
        make_shadow(key="BTCUSDT|LONG|c2", id_=2), make_result(delay=70.0), make_candidate(),
    )  # SKIPPED por tarde
    await arm.activate()

    block = await arm.health_block()

    assert block["enabled"] is True and block["active"] is True
    assert block["opened_today"] == 1 and block["skipped_today"] == 1
    assert block["armed_since"] is not None
    assert not any("pnl" in k for k in block)
