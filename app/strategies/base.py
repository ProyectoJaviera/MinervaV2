"""Interfaz base para estrategias por reglas.

En Fase 1 la senal de la estrategia ES la decision (no hay score de
confluencia ni LLM todavia). El motor de confluencia que combina varias
estrategias + Claude llega en Fase 3/4."""

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
