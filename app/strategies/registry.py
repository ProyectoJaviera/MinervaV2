"""Registro de estrategias candidatas (Fase 2, docs/FASE2_PLAN.md seccion B).

`EXPERIMENTAL_STRATEGIES` marca las que NO participan en el veredicto
global de descarte (solo se reportan) -- por ahora, `funding_contrarian`.
"""

from __future__ import annotations

from app.strategies.base import BaseStrategy
from app.strategies.donchian_breakout import DonchianBreakoutStrategy
from app.strategies.ema_cross import EMACrossStrategy
from app.strategies.funding_contrarian import FundingContrarianStrategy
from app.strategies.mean_reversion_rsi_bb import MeanReversionRSIBBStrategy
from app.strategies.trend_atr_stop import TrendATRStopStrategy

STRATEGIES: dict[str, BaseStrategy] = {
    "ema_cross_9_21": EMACrossStrategy(fast=9, slow=21),
    "trend_atr_stop_9_21_50": TrendATRStopStrategy(fast=9, slow=21, trend=50),
    "mean_reversion_rsi14_bb20": MeanReversionRSIBBStrategy(),
    "donchian_breakout_20": DonchianBreakoutStrategy(period=20),
    "funding_contrarian_experimental": FundingContrarianStrategy(),
}

# Timeframes en los que se evalua cada estrategia en el backtest de Fase 2
# (docs/FASE2_PLAN.md seccion B). Donchian se prueba en dos timeframes.
STRATEGY_TIMEFRAMES: dict[str, list[str]] = {
    "ema_cross_9_21": ["4h"],
    "trend_atr_stop_9_21_50": ["4h"],
    "mean_reversion_rsi14_bb20": ["1h"],
    "donchian_breakout_20": ["4h", "1d"],
    "funding_contrarian_experimental": ["4h"],
}

EXPERIMENTAL_STRATEGIES = {"funding_contrarian_experimental"}


def get_strategy(name: str) -> BaseStrategy:
    try:
        return STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"Estrategia desconocida: {name}") from exc
