"""Tests de la logica de veredicto (app/backtesting/report.py): criterios
de descarte combinados, estrategias experimentales exentas, y evidencia
insuficiente -- docs/FASE2_PLAN.md seccion C y los ajustes del usuario
(punto 2)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.backtesting.engine import RawTrade
from app.backtesting.report import _build_verdict
from app.config import Settings
from app.persistence.models import Side

START_MS = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)


def _trade(pnl_net: float, month: int, symbol: str = "BTCUSDT") -> RawTrade:
    t = datetime(2022, month, 15, tzinfo=UTC)
    return RawTrade(
        strategy="x", symbol=symbol, timeframe="4h", side=Side.LONG,
        entry_time=t, exit_time=t, entry_price=100.0, exit_price=100.0, qty=1.0,
        margin_usdt=10.0, leverage=10, fee_entry_usdt=0.0, fee_exit_usdt=0.0,
        slippage_cost_usdt=0.0, funding_paid_usdt=0.0, funding_is_approximated=False,
        pnl_gross_usdt=pnl_net, pnl_net_usdt=pnl_net, close_reason="TP",
        sl_margin_loss_pct=None,
    )


def make_settings(**overrides) -> Settings:
    defaults = dict(
        BACKTEST_INITIAL_CAPITAL=100.0, BACKTEST_MIN_PROFIT_FACTOR=1.2,
        BACKTEST_MIN_TRADES_TOTAL=10, BACKTEST_MIN_TRADES_PER_CELL=1,
        BACKTEST_MIN_PCT_SYMBOLS_PF_GT1=0.50, BACKTEST_MIN_PCT_FOLDS_POSITIVE=0.50,
        BACKTEST_STRESS_FEE_MULTIPLIER=2.0, BACKTEST_STRESS_SLIPPAGE_MULTIPLIER=2.0,
        BACKTEST_MAX_DRAWDOWN_PCT=0.50, BACKTEST_CONCENTRATION_LIMIT_PCT=0.40,
        BACKTEST_OOS_SPLIT_PCT=0.30, BACKTEST_WALK_FORWARD_FOLD_MONTHS=6,
        BACKTEST_WALK_FORWARD_STEP_MONTHS=2, BACKTEST_CONTROL_SYMBOLS="BTCUSDT,ETHUSDT",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def _winning_set() -> list[RawTrade]:
    # 12 operaciones ganadoras repartidas en el ano, en 3 simbolos (para que
    # ninguno concentre mas del 40% del PnL), todas con PF>1.
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    return [_trade(5.0, month, symbol=symbols[month % 3]) for month in range(1, 13)]


def test_winning_strategy_is_not_discarded():
    settings = make_settings()
    verdict = _build_verdict(
        "trend_atr_stop_9_21_50", _winning_set(), oos_boundary_ms=0,
        start_ms=START_MS, end_ms=END_MS, combos_tested=2, settings=settings,
    )
    assert verdict.discarded is False
    assert verdict.evidence_insufficient is False
    assert verdict.discard_reasons_json is None


def test_losing_strategy_is_discarded_with_pf_reason():
    trades = [_trade(-5.0, month) for month in range(1, 13)]
    settings = make_settings()
    verdict = _build_verdict(
        "donchian_breakout_20", trades, oos_boundary_ms=0,
        start_ms=START_MS, end_ms=END_MS, combos_tested=1, settings=settings,
    )
    assert verdict.discarded is True
    assert verdict.discard_reasons_json is not None
    assert "PF OOS" in verdict.discard_reasons_json


def test_experimental_strategy_never_discarded_even_if_losing():
    trades = [_trade(-5.0, month) for month in range(1, 13)]
    settings = make_settings()
    verdict = _build_verdict(
        "funding_contrarian_experimental", trades, oos_boundary_ms=0,
        start_ms=START_MS, end_ms=END_MS, combos_tested=1, settings=settings,
    )
    assert verdict.is_experimental is True
    assert verdict.discarded is False
    assert verdict.discard_reasons_json is None


def test_too_few_trades_marks_evidence_insufficient_not_discarded():
    trades = [_trade(-5.0, 1), _trade(-5.0, 2)]  # 2 < BACKTEST_MIN_TRADES_TOTAL (10)
    settings = make_settings()
    verdict = _build_verdict(
        "ema_cross_9_21", trades, oos_boundary_ms=0,
        start_ms=START_MS, end_ms=END_MS, combos_tested=1, settings=settings,
    )
    assert verdict.evidence_insufficient is True
    assert verdict.discarded is False


def test_concentration_triggers_discard_despite_good_pf():
    # Una sola operacion domina casi todo el PnL neto positivo.
    trades = [_trade(100.0, 1)] + [_trade(0.5, m) for m in range(2, 13)]
    settings = make_settings()
    verdict = _build_verdict(
        "mean_reversion_rsi14_bb20", trades, oos_boundary_ms=0,
        start_ms=START_MS, end_ms=END_MS, combos_tested=1, settings=settings,
    )
    assert verdict.discarded is True
    assert "Concentracion" in verdict.discard_reasons_json


def test_is_oos_degradation_report_splits_trades_by_boundary():
    """Tarea 4: PF y numero de operaciones de IS junto a los de OOS --
    meses 1-6 (perdedores) quedan del lado IS, meses 7-12 (ganadores) del
    lado OOS, segun donde caiga `oos_boundary_ms`."""
    is_trades = [_trade(-5.0, month) for month in range(1, 7)]
    oos_trades = [_trade(5.0, month) for month in range(7, 13)]
    settings = make_settings()
    oos_boundary_ms = int(datetime(2022, 7, 1, tzinfo=UTC).timestamp() * 1000)

    verdict = _build_verdict(
        "ema_cross_9_21", is_trades + oos_trades, oos_boundary_ms=oos_boundary_ms,
        start_ms=START_MS, end_ms=END_MS, combos_tested=1, settings=settings,
    )

    assert verdict.is_trades_count == 6
    assert verdict.oos_trades_count == 6
    assert verdict.pf_is_aggregate == 0.0  # todas perdedoras -> gross_profit 0
    assert verdict.pf_oos_aggregate is None  # todas ganadoras, sin perdidas -> PF infinito -> None


def test_oos_only_portfolio_simulation_ignores_is_trades():
    """La simulacion de cartera "solo OOS" debe reflejar unicamente las
    operaciones OOS -- aunque haya muchas mas operaciones IS, el numero de
    trades incluidos (mediana) en la version OOS no puede superar la
    cantidad de operaciones OOS disponibles."""
    is_trades = [_trade(5.0, month, symbol="BTCUSDT") for month in range(1, 7)]
    oos_trades = [_trade(5.0, month, symbol="ETHUSDT") for month in range(7, 10)]
    settings = make_settings(BACKTEST_PORTFOLIO_SIM_RUNS=20)
    oos_boundary_ms = int(datetime(2022, 7, 1, tzinfo=UTC).timestamp() * 1000)

    verdict = _build_verdict(
        "ema_cross_9_21", is_trades + oos_trades, oos_boundary_ms=oos_boundary_ms,
        start_ms=START_MS, end_ms=END_MS, combos_tested=1, settings=settings,
    )

    assert verdict.oos_trades_count == 3
    assert verdict.portfolio_oos_trades_included_median <= 3
    # La version con todo el periodo (IS+OOS) si puede incluir las 9.
    assert verdict.portfolio_trades_included_median <= 9
