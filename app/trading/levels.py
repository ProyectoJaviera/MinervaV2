"""Niveles de riesgo de una posicion (SL, TP, trailing, liquidacion), calculados
al precio REAL de llenado -- nunca al precio de la senal. Los niveles absolutos
que devuelve la estrategia se respetan; los que faltan se completan con el SL/TP
de respaldo porcentual, igual que en el backtest."""

from __future__ import annotations

from dataclasses import dataclass

from app.backtesting.liquidation import compute_liquidation_price
from app.config import Settings
from app.persistence.models import Side
from app.strategies.base import Signal
from app.trading.sl_calc import fallback_sl_tp_prices


@dataclass(frozen=True)
class StrategyLevels:
    """Lo que la estrategia devuelve tal cual: precios absolutos o None."""

    stop_price: float | None = None
    take_profit_price: float | None = None
    trailing_distance: float | None = None


@dataclass(frozen=True)
class TradeLevels:
    sl_price: float | None
    tp_price: float | None
    trailing_distance: float | None
    liq_price: float


def resolve_trade_levels(
    side: Side,
    fill_price: float,
    leverage: int,
    notional_usdt: float,
    margin_tiers_json: str | None,
    planned: StrategyLevels | None,
    settings: Settings,
) -> TradeLevels:
    """`planned=None` (entrada manual, sin estrategia) no tiene SL ni TP; la
    liquidacion se calcula siempre. Un SL o TP planeado del lado equivocado
    respecto al llenado se rechaza: la posicion se cerraria en el primer tick
    sin haber existido."""
    liq = compute_liquidation_price(side, fill_price, leverage, notional_usdt, margin_tiers_json)
    if planned is None:
        return TradeLevels(sl_price=None, tp_price=None, trailing_distance=None, liq_price=liq)

    sl = planned.stop_price
    tp = planned.take_profit_price
    trailing = planned.trailing_distance
    fallback_sl, fallback_tp = fallback_sl_tp_prices(Signal(side.value), fill_price, settings)
    if sl is None:
        sl = fallback_sl
    if tp is None and trailing is None:
        tp = fallback_tp

    is_long = side == Side.LONG
    if (sl >= fill_price) if is_long else (sl <= fill_price):
        raise ValueError(
            f"SL planeado {sl} ya superado al llenar a {fill_price} ({side.value}); "
            "la entrada se descarta"
        )
    if tp is not None and ((tp <= fill_price) if is_long else (tp >= fill_price)):
        raise ValueError(
            f"TP planeado {tp} del lado equivocado respecto a {fill_price} ({side.value}); "
            "se ejecutaria en el primer tick: la entrada se descarta"
        )
    return TradeLevels(sl_price=sl, tp_price=tp, trailing_distance=trailing, liq_price=liq)
