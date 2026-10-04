"""Trailing (subfase 3.4): el stop solo se mueve a favor, nunca se afloja."""

from __future__ import annotations

import pytest

from app.trading.stop_engine import advance_trailing_stop


def test_long_trailing_moves_up_with_new_high():
    best, eff = advance_trailing_stop(True, 100.0, 95.0, 3.0, favorable_extreme=110.0)
    assert best == pytest.approx(110.0)
    assert eff == pytest.approx(107.0)


def test_long_trailing_never_loosens_on_a_lower_price():
    best, eff = advance_trailing_stop(True, 110.0, 107.0, 3.0, favorable_extreme=104.0)
    assert best == pytest.approx(110.0)
    assert eff == pytest.approx(107.0)


def test_long_trailing_candidate_below_original_stop_keeps_original():
    # best 100 - trailing 10 = 90 < SL original 95: el stop NO se afloja.
    _, eff = advance_trailing_stop(True, 100.0, 95.0, 10.0, favorable_extreme=100.0)
    assert eff == pytest.approx(95.0)


def test_short_trailing_moves_down_and_never_up():
    best, eff = advance_trailing_stop(False, 100.0, 105.0, 3.0, favorable_extreme=90.0)
    assert best == pytest.approx(90.0)
    assert eff == pytest.approx(93.0)
    best2, eff2 = advance_trailing_stop(False, best, eff, 3.0, favorable_extreme=95.0)
    assert best2 == pytest.approx(90.0)
    assert eff2 == pytest.approx(93.0)
