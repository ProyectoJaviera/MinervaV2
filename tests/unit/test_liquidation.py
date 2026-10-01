from __future__ import annotations

import json

import pytest

from app.backtesting.liquidation import (
    DEFAULT_MMR_FALLBACK,
    compute_liquidation_price,
    maintenance_margin_rate_for_notional,
)
from app.persistence.models import Side


def test_fallback_mmr_when_no_tiers():
    assert maintenance_margin_rate_for_notional(None, 1000.0) == DEFAULT_MMR_FALLBACK


def test_fallback_mmr_on_invalid_json():
    assert maintenance_margin_rate_for_notional("not json", 1000.0) == DEFAULT_MMR_FALLBACK


def test_picks_correct_tier_by_notional():
    tiers = json.dumps([
        {"startValue": 0, "endValue": 5000, "maintenanceMarginRate": 0.004},
        {"startValue": 5000, "endValue": 25000, "maintenanceMarginRate": 0.01},
    ])
    assert maintenance_margin_rate_for_notional(tiers, 1000.0) == pytest.approx(0.004)
    assert maintenance_margin_rate_for_notional(tiers, 10000.0) == pytest.approx(0.01)


def test_long_liquidation_price_below_entry_at_10x():
    # leverage 10x, MMR 0.4%: liq = entry * (1 - 0.10 + 0.004) = entry * 0.904
    tiers = json.dumps([{"startValue": 0, "endValue": 1e9, "maintenanceMarginRate": 0.004}])
    liq = compute_liquidation_price(
        Side.LONG, entry_price=100.0, leverage=10, notional=1000.0, margin_tiers_json=tiers
    )
    assert liq == pytest.approx(90.4)
    assert liq < 100.0


def test_short_liquidation_price_above_entry_at_10x():
    tiers = json.dumps([{"startValue": 0, "endValue": 1e9, "maintenanceMarginRate": 0.004}])
    liq = compute_liquidation_price(
        Side.SHORT, entry_price=100.0, leverage=10, notional=1000.0, margin_tiers_json=tiers
    )
    assert liq == pytest.approx(109.6)
    assert liq > 100.0


def test_higher_leverage_moves_liquidation_closer_to_entry():
    tiers = json.dumps([{"startValue": 0, "endValue": 1e9, "maintenanceMarginRate": 0.004}])
    liq_10x = compute_liquidation_price(Side.LONG, 100.0, 10, 1000.0, tiers)
    liq_20x = compute_liquidation_price(Side.LONG, 100.0, 20, 2000.0, tiers)
    assert liq_20x > liq_10x  # mas apalancamiento -> liquidacion mas cerca de la entrada
