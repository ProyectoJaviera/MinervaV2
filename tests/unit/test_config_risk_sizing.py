"""Tests de la formula de sizing de riesgo (ver docs/FASE0.md, verificacion de
coherencia con capital=100, margen=10, tope 10%/activo, 3 posiciones max)."""

from __future__ import annotations

import pytest

from app.config import Settings


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0,
        MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=3,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_default_case_allows_exactly_one_default_sized_position():
    settings = make_settings()
    allowed = settings.max_margin_for_new_trade(
        current_capital=100.0, margin_committed_on_symbol=0.0, margin_committed_total=0.0
    )
    assert allowed == pytest.approx(10.0)


def test_asset_already_at_cap_returns_zero():
    settings = make_settings()
    allowed = settings.max_margin_for_new_trade(
        current_capital=100.0, margin_committed_on_symbol=10.0, margin_committed_total=10.0
    )
    assert allowed == pytest.approx(0.0)


def test_total_positions_limit_binds_before_per_asset_cap():
    settings = make_settings()
    # 3 posiciones ya abiertas en otros simbolos (30 USDT comprometidos): no
    # queda espacio para una cuarta, aunque el simbolo nuevo no tenga margen
    # comprometido todavia.
    allowed = settings.max_margin_for_new_trade(
        current_capital=100.0, margin_committed_on_symbol=0.0, margin_committed_total=30.0
    )
    assert allowed == pytest.approx(0.0)


def test_shrinks_after_losses_reduce_capital():
    settings = make_settings()
    # El capital cayo a 50 USDT tras perdidas: el tope por activo (10%) tambien
    # cae a 5 USDT, por debajo del margen configurado de 10.
    allowed = settings.max_margin_for_new_trade(
        current_capital=50.0, margin_committed_on_symbol=0.0, margin_committed_total=0.0
    )
    assert allowed == pytest.approx(5.0)


def test_never_returns_negative():
    settings = make_settings()
    allowed = settings.max_margin_for_new_trade(
        current_capital=10.0, margin_committed_on_symbol=50.0, margin_committed_total=50.0
    )
    assert allowed == 0.0
