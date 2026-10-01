from __future__ import annotations

import pytest

from app.backtesting.funding import build_funding_series, funding_cost_for_bar
from app.persistence.models import Side


def test_no_real_events_returns_zero_and_all_approximated():
    rates, approx = build_funding_series([100, 200, 300], [])
    assert rates == [0.0, 0.0, 0.0]
    assert approx == [True, True, True]


def test_bars_before_earliest_real_event_use_median_and_are_approximated():
    real_events = [(1000, 0.0001), (2000, 0.0003), (3000, 0.0002)]
    rates, approx = build_funding_series([0, 500], real_events)
    median = 0.0002  # mediana de [0.0001, 0.0002, 0.0003]
    assert rates == [median, median]
    assert approx == [True, True]


def test_bars_at_or_after_real_events_forward_fill_and_are_real():
    real_events = [(1000, 0.0001), (2000, 0.0003)]
    rates, approx = build_funding_series([1000, 1500, 2000, 2500], real_events)
    assert rates == [0.0001, 0.0001, 0.0003, 0.0003]
    assert approx == [False, False, False, False]


def test_empty_bar_list_returns_empty():
    assert build_funding_series([], [(1, 0.0001)]) == ([], [])


def test_funding_cost_long_pays_on_positive_rate():
    cost = funding_cost_for_bar(
        notional=1000.0, rate=0.0004, side=Side.LONG, bar_duration_hours=8.0,
        funding_interval_hours=8.0,
    )
    assert cost == pytest.approx(0.4)  # 1000 * 0.0004 * 1 (prorrateo 8/8 = 1)


def test_funding_cost_short_receives_on_positive_rate():
    cost = funding_cost_for_bar(
        notional=1000.0, rate=0.0004, side=Side.SHORT, bar_duration_hours=8.0,
        funding_interval_hours=8.0,
    )
    assert cost == pytest.approx(-0.4)  # SHORT recibe cuando el funding es positivo


def test_funding_cost_prorated_by_bar_duration():
    cost_4h = funding_cost_for_bar(
        1000.0, 0.0004, Side.LONG, bar_duration_hours=4.0, funding_interval_hours=8.0
    )
    cost_8h = funding_cost_for_bar(
        1000.0, 0.0004, Side.LONG, bar_duration_hours=8.0, funding_interval_hours=8.0
    )
    assert cost_4h == pytest.approx(cost_8h / 2)
