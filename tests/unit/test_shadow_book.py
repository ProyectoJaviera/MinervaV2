"""Operaciones sombra (subfase 3.5): paridad de PnL con la cuenta paper, agrupacion
de senales de estrategias distintas en UNA operacion, independencia de cupos y
margen, deduplicacion, ciclo de vida con el monitor, atribucion en el reporte y
niveles invalidos."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.persistence.models import Side, TradeStatus
from app.persistence.repositories import shadow_repo, trades_repo
from app.trading import shadow_report
from app.trading.levels import StrategyLevels
from app.trading.position_monitor import PositionMonitor
from app.trading.shadow_book import ShadowBook
from app.trading.signal_generator import SignalCandidate

CANDLE = datetime(2026, 1, 1, 4, tzinfo=UTC)


class FakeRest:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_tickers(self, symbol: str | None = None):
        return [{"symbol": symbol, "lastPrice": str(self.price), "markPrice": str(self.price)}]

    async def get_funding_rate_history(self, *args, **kwargs):
        return []

    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise AssertionError(f"llamada de red inesperada: {name}")

        return _fail


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006, MAKER_FEE_PCT=0.0002,
        INITIAL_CAPITAL_USDT=100.0, MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=3, MAX_SAME_DIRECTION_POSITIONS=3,
        LIVE_SL_MARGIN_CAP_PCT=50.0, MAX_DAILY_LOSS_PCT=0.9, MAX_DRAWDOWN_PCT=0.9,
        REAL_ACCOUNT_ELIGIBLE_STRATEGIES="ema_cross_9_21,donchian_breakout_20",
        BACKTEST_SLIPPAGE_BPS=5.0, AUTO_OPEN_WITHOUT_LLM=False,
        TP_GAP_TOLERANCE_PCT=0.002, TICK_STALE_SECONDS=60.0, WS_STALE_AFTER_SECONDS=45.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def candidate(
    strategies=("ema_cross_9_21",), side=Side.LONG, price=100.0,
    levels=None, candle=CANDLE, symbol="BTCUSDT",
) -> SignalCandidate:
    strategies = sorted(strategies)
    return SignalCandidate(
        symbol=symbol, side=side, candle_close_time=candle,
        contributing_strategies=strategies, sl_margin_loss_pct=50.0,
        levels_by_strategy={s: (levels or StrategyLevels()) for s in strategies},
        price_by_strategy={s: price for s in strategies},
    )


# --- paridad de PnL con la cuenta paper ---------------------------------------


@pytest.mark.asyncio
async def test_shadow_and_paper_close_with_identical_pnl_for_the_same_signal(db):
    settings = make_settings()
    rest = FakeRest(100.0)
    paper = PaperBackend(db, rest, settings)
    real = await paper.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, is_manual=True,
    )
    shadow = ShadowBook(db, settings)
    sim = await shadow.open_candidate(candidate())
    assert sim is not None

    rest.price = 110.0
    real_closed = await paper.close_position(real.id, reason="MANUAL")
    sim_closed = await shadow.close(sim, 110.0, "MANUAL")

    assert sim_closed.fee_entry_usdt == pytest.approx(real.fee_entry_usdt)
    assert sim_closed.fee_exit_usdt == pytest.approx(real_closed.fee_exit_usdt)
    assert sim_closed.slippage_entry_usdt == pytest.approx(real.slippage_entry_usdt)
    assert sim_closed.slippage_exit_usdt == pytest.approx(real_closed.slippage_exit_usdt)
    assert sim_closed.pnl_gross_usdt == pytest.approx(real_closed.pnl_gross_usdt)
    assert sim_closed.pnl_net_usdt == pytest.approx(real_closed.pnl_net_usdt)


# --- agrupacion, dedup e independencia de cupos ----------------------------------


@pytest.mark.asyncio
async def test_two_strategies_on_the_same_signal_produce_one_shadow_trade(db, monkeypatch):
    settings = make_settings()
    rest = FakeRest(100.0)
    backend = PaperBackend(db, rest, settings)
    shadow = ShadowBook(db, settings)
    scheduler = Scheduler(db, rest, backend, settings, shadow=shadow)
    grouped = candidate(strategies=("ema_cross_9_21", "donchian_breakout_20"))

    async def fake_generation(db_arg, rest_arg, settings_arg):
        return [grouped]

    monkeypatch.setattr("app.core.scheduler.run_signal_generation_cycle", fake_generation)
    await scheduler.run_cycle()

    trades = await shadow_repo.get_open(db)
    assert len(trades) == 1
    assert trades[0].contributing_strategies == ["donchian_breakout_20", "ema_cross_9_21"]
    assert trades[0].llm_decision == "SIN_LLM"
    assert trades[0].executed_in_real_account is False


@pytest.mark.asyncio
async def test_shadow_ignores_position_limits_and_auto_flag(db):
    settings = make_settings(MAX_SIMULTANEOUS_POSITIONS=0, AUTO_OPEN_WITHOUT_LLM=False)
    shadow = ShadowBook(db, settings)
    trade = await shadow.open_candidate(candidate())
    assert trade is not None and trade.status == TradeStatus.OPEN


@pytest.mark.asyncio
async def test_same_signal_group_is_never_opened_twice(db):
    settings = make_settings()
    shadow = ShadowBook(db, settings)
    first = await shadow.open_candidate(candidate())
    second = await shadow.open_candidate(candidate())
    assert first is not None and second is None
    assert len(await shadow_repo.get_open(db)) == 1


@pytest.mark.asyncio
async def test_different_candles_are_separate_shadow_trades(db):
    settings = make_settings()
    shadow = ShadowBook(db, settings)
    await shadow.open_candidate(candidate(candle=CANDLE))
    await shadow.open_candidate(candidate(candle=datetime(2026, 1, 1, 8, tzinfo=UTC)))
    assert len(await shadow_repo.get_open(db)) == 2


@pytest.mark.asyncio
async def test_real_open_of_the_same_signal_marks_the_shadow_as_executed(db, monkeypatch):
    settings = make_settings(AUTO_OPEN_WITHOUT_LLM=True)
    rest = FakeRest(100.0)
    backend = PaperBackend(db, rest, settings)
    scheduler = Scheduler(db, rest, backend, settings, shadow=ShadowBook(db, settings))
    cand = candidate(levels=StrategyLevels(stop_price=95.0, take_profit_price=110.0))

    async def fake_generation(db_arg, rest_arg, settings_arg):
        return [cand]

    monkeypatch.setattr("app.core.scheduler.run_signal_generation_cycle", fake_generation)
    await scheduler.run_cycle()

    shadow_rows = await shadow_repo.get_open(db)
    assert len(shadow_rows) == 1 and shadow_rows[0].executed_in_real_account is True
    assert len(await trades_repo.get_open_positions(db, "BTCUSDT")) == 1


@pytest.mark.asyncio
async def test_invalid_levels_for_the_shadow_are_skipped_without_crashing(db):
    settings = make_settings()
    shadow = ShadowBook(db, settings)
    bad = candidate(levels=StrategyLevels(stop_price=105.0))  # SL del lado equivocado
    assert await shadow.open_candidate(bad) is None
    assert await shadow_repo.get_open(db) == []


# --- ciclo de vida con el monitor ----------------------------------------------


@pytest.mark.asyncio
async def test_monitor_closes_a_shadow_trade_on_its_stop_with_the_same_rules(db):
    settings = make_settings()
    rest = FakeRest(100.0)
    paper = PaperBackend(db, rest, settings)
    shadow = ShadowBook(db, settings)
    monitor = PositionMonitor(db, rest, paper, settings, shadow=shadow)
    trade = await shadow.open_candidate(
        candidate(levels=StrategyLevels(stop_price=95.0, take_profit_price=110.0))
    )
    await monitor._reload_trades()
    await monitor.on_tick("BTCUSDT", 94.0)

    closed = await shadow_repo.get(db, trade.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.close_reason == "SL"
    assert closed.exit_price == pytest.approx(94.0)
    assert closed.fill_source == "TICK"
    assert closed.pnl_net_usdt < 0


# --- reporte por estrategia con atribucion --------------------------------------


@pytest.mark.asyncio
async def test_report_attributes_each_group_to_every_contributor_without_double_counting(db):
    settings = make_settings()
    shadow = ShadowBook(db, settings)
    rest = FakeRest(100.0)
    monitor = PositionMonitor(db, rest, PaperBackend(db, rest, settings), settings, shadow=shadow)
    levels = StrategyLevels(stop_price=95.0, take_profit_price=110.0)
    win = await shadow.open_candidate(candidate(
        strategies=("ema_cross_9_21", "donchian_breakout_20"), candle=CANDLE, levels=levels,
    ))
    loss = await shadow.open_candidate(candidate(
        strategies=("ema_cross_9_21",), candle=datetime(2026, 1, 1, 8, tzinfo=UTC), levels=levels,
    ))
    await monitor._reload_trades()
    await monitor.on_tick("BTCUSDT", 111.0)  # gana el primer grupo (TP)
    await monitor.on_tick("BTCUSDT", 94.0)   # pierde el segundo (SL)

    win_closed = await shadow_repo.get(db, win.id)
    loss_closed = await shadow_repo.get(db, loss.id)
    rows = {r.strategy: r for r in await shadow_report.summarize_by_strategy(db)}
    totals = await shadow_report.summarize_totals(db)

    assert rows["ema_cross_9_21"].groups == 2 and rows["ema_cross_9_21"].closed == 2
    assert rows["donchian_breakout_20"].groups == 1 and rows["donchian_breakout_20"].closed == 1
    assert totals.groups == 2
    assert totals.pnl_net_total == pytest.approx(win_closed.pnl_net_usdt + loss_closed.pnl_net_usdt)
    # La estrategia ema contribuyo a ambos grupos: su PnL es la suma de los dos.
    assert rows["ema_cross_9_21"].pnl_net_total == pytest.approx(
        win_closed.pnl_net_usdt + loss_closed.pnl_net_usdt
    )
    assert "no suman el total" in shadow_report.render_markdown(list(rows.values()), totals)
