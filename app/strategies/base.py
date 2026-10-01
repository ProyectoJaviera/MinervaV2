"""Interfaz base para estrategias por reglas.

En Fase 1 la senal de la estrategia ES la decision (no hay score de
confluencia ni LLM todavia). El motor de confluencia que combina varias
estrategias + Claude llega en Fase 3/4.

Fase 2 agrega tres metodos opcionales (default `None`) para que cada
estrategia pueda definir su propia gestion de riesgo (ATR, nivel de banda,
etc.) sin romper la interfaz ni las estrategias existentes que no los
necesiten: `stop_price`, `take_profit_price`, `trailing_distance`. Si una
estrategia no los implementa, el motor de backtest (`app/backtesting/`)
aplica un SL/TP porcentual de respaldo configurable."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

import pandas as pd


class Signal(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


class BaseStrategy(ABC):
    name: str

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> Signal:
        """Recibe un DataFrame OHLCV ordenado por tiempo ascendente (columnas
        'open', 'high', 'low', 'close') y devuelve la senal para la ULTIMA vela
        cerrada."""
        raise NotImplementedError

    def stop_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        """Precio absoluto de stop loss para una entrada en `entry_price`.
        `None` -> el motor usa un SL porcentual de respaldo."""
        return None

    def take_profit_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        """Precio absoluto de take profit. `None` -> el motor usa un TP
        porcentual de respaldo."""
        return None

    def trailing_distance(self, df: pd.DataFrame) -> float | None:
        """Distancia (en precio, siempre positiva) que mantiene el trailing
        stop respecto del mejor precio alcanzado. `None` -> sin trailing."""
        return None
