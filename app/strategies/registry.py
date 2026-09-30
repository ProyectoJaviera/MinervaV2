"""Registro de estrategias disponibles. Fase 2 anadira mas candidatas y el
motor de backtesting que decide cuales se descartan."""

from __future__ import annotations

from app.strategies.base import BaseStrategy
from app.strategies.ema_cross import EMACrossStrategy

STRATEGIES: dict[str, BaseStrategy] = {
    "ema_cross_9_21": EMACrossStrategy(fast=9, slow=21),
}


def get_strategy(name: str) -> BaseStrategy:
    try:
        return STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"Estrategia desconocida: {name}") from exc
