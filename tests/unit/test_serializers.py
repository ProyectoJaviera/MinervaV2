"""El choke point de integridad de la medicion (Subfase C del plan pre-Fase
5 aprobado 2026-10-14): `app.api.serializers.shadow_trade_to_public_dict`.
La parte mas critica del plan -- con `reveal_outcome=False` los campos de
resultado deben estar AUSENTES del dict, no `None`."""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.serializers import shadow_trade_to_public_dict
from app.persistence.models import ShadowTrade, Side, TradeStatus

CANDLE = datetime(2026, 1, 1, 4, tzinfo=UTC)

OUTCOME_FIELDS = {"pnl_gross_usdt", "pnl_net_usdt", "exit_price", "close_reason", "closed_at"}


def make_trade(**overrides) -> ShadowTrade:
    defaults = dict(
        id=1, symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21",
        status=TradeStatus.CLOSED, leverage=10, margin_usdt=5.0, notional_usdt=50.0,
        qty=1.0, entry_price=100.0, opened_at=CANDLE, exit_price=105.0,
        pnl_gross_usdt=5.0, pnl_net_usdt=4.5, close_reason="TP",
        closed_at=CANDLE, contributing_strategies=["ema_cross_9_21"],
        signal_group_key="k1", candle_close_time=CANDLE, llm_decision="APROBADA",
        fee_entry_usdt=0.1, funding_paid_usdt=0.05, best_price=106.0,
    )
    defaults.update(overrides)
    return ShadowTrade(**defaults)


def test_outcome_fields_are_absent_not_none_when_the_gate_is_closed():
    trade = make_trade()

    data = shadow_trade_to_public_dict(trade, reveal_outcome=False)

    for field in OUTCOME_FIELDS:
        assert field not in data, f"{field} no deberia estar presente con el gate cerrado"


def test_outcome_fields_are_present_with_their_real_values_when_the_gate_is_open():
    trade = make_trade()

    data = shadow_trade_to_public_dict(trade, reveal_outcome=True)

    assert data["pnl_gross_usdt"] == 5.0
    assert data["pnl_net_usdt"] == 4.5
    assert data["exit_price"] == 105.0
    assert data["close_reason"] == "TP"
    assert data["closed_at"] == CANDLE.isoformat()


def test_always_visible_fields_are_present_regardless_of_the_gate():
    trade = make_trade()

    for reveal in (False, True):
        data = shadow_trade_to_public_dict(trade, reveal_outcome=reveal)
        assert data["symbol"] == "BTCUSDT"
        assert data["side"] == "LONG"
        assert data["llm_decision"] == "APROBADA"
        assert data["entry_price"] == 100.0
        assert data["sl_margin_loss_pct"] is None  # None explicito, no ausente


def test_cost_and_trailing_fields_are_never_included_regardless_of_the_gate():
    """Fuera del whitelist en CUALQUIER caso -- no son PnL explicito, pero
    son senales blandas de resultado/costo que esta vista no necesita."""
    trade = make_trade()

    for reveal in (False, True):
        data = shadow_trade_to_public_dict(trade, reveal_outcome=reveal)
        for field in ("fee_entry_usdt", "fee_exit_usdt", "funding_paid_usdt",
                      "slippage_entry_usdt", "slippage_exit_usdt", "best_price",
                      "effective_stop"):
            assert field not in data


def test_an_open_trade_with_no_closed_at_serializes_cleanly_when_revealed():
    trade = make_trade(status=TradeStatus.OPEN, exit_price=None, pnl_gross_usdt=None,
                        pnl_net_usdt=None, close_reason=None, closed_at=None)

    data = shadow_trade_to_public_dict(trade, reveal_outcome=True)

    assert data["status"] == "OPEN"
    assert data["closed_at"] is None
    assert data["exit_price"] is None


def test_never_uses_model_dump_so_a_future_field_is_excluded_by_default():
    """Documenta la garantia de "falla cerrado": un campo que ShadowTrade
    tenga pero que esta funcion no conozca jamas aparece en la salida."""
    trade = make_trade()
    data = shadow_trade_to_public_dict(trade, reveal_outcome=True)

    assert "reconciliation_failed" not in data
    assert "decision_source" not in data
    assert "trailing_distance" not in data
