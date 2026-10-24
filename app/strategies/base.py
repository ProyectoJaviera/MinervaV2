"""Interfaz base para estrategias por reglas.

La senal de la estrategia no abre nada por si sola: el generador de senales (`app/trading/
signal_generator.py`) la registra, se simula como operacion sombra y Claude la etiqueta `APROBADA`
o `RECHAZADA`; la cuenta paper solo abre las aprobadas a tiempo. No hay un puntaje de confluencia
entre estrategias (docs/INFORME_FINAL.md, "Cumplimiento de la especificacion").

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
    """Senal de una estrategia sobre la ultima vela cerrada: `LONG`, `SHORT` o `HOLD`."""
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


class BaseStrategy(ABC):
    """Clase base de las estrategias: `evaluate` decide la senal; lo demas es opcional.

    Una estrategia solo emite una senal; no abre nada. `stop_price`, `take_profit_price` y
    `trailing_distance` devuelven `None` por defecto (el motor usa entonces un SL/TP
    porcentual de respaldo). `precompute` agrega una sola vez las columnas de indicadores
    a toda la serie, y `evaluate` las usa si estan presentes (asi el backtest no recalcula
    en cada vela; el resultado es identico porque todos los indicadores son causales).

    Attributes:
        name: nombre unico de la estrategia (clave del registro y de las tablas).
    """
    name: str

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> Signal:
        """Decide la senal para la ULTIMA vela cerrada.

        Args:
            df: velas OHLCV en orden cronologico ascendente (columnas `open`, `high`, `low`,
                `close`, y las que `precompute` haya agregado).

        Returns:
            `Signal.LONG`, `Signal.SHORT` o `Signal.HOLD`.

        Raises:
            NotImplementedError: en la clase base; cada estrategia debe implementarlo.
        """
        raise NotImplementedError

    def stop_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        """Precio absoluto del stop loss para una entrada en `entry_price`.

        Args:
            df: velas hasta la de la senal.
            side: direccion de la entrada.
            entry_price: precio de entrada.

        Returns:
            El precio del SL, o `None` para que el motor use el SL porcentual de respaldo.
        """
        return None

    def take_profit_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        """Precio absoluto del take profit.

        Args:
            df: velas hasta la de la senal.
            side: direccion de la entrada.
            entry_price: precio de entrada.

        Returns:
            El precio del TP, o `None` para que el motor use el TP porcentual de respaldo.
        """
        return None

    def trailing_distance(self, df: pd.DataFrame) -> float | None:
        """Distancia (en precio, siempre positiva) que mantiene el trailing stop del mejor precio.

        Args:
            df: velas hasta la de la senal.

        Returns:
            La distancia, o `None` si la estrategia no usa trailing.
        """
        return None

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrega a `df` las columnas de indicadores, calculadas UNA vez sobre toda la serie.

        Correccion de rendimiento post-Fase 2: sin esto el motor de backtest recalculaba cada
        indicador desde cero en cada vela. Todos los indicadores son causales (jamas leen datos
        futuros), asi que precalcular da EXACTAMENTE el valor que se obtendria recomputando en
        cada vela con una ventana suficiente; no es una aproximacion. Por defecto no agrega
        nada. Una estrategia que lo sobreescribe tambien debe leer la columna si esta presente.

        Args:
            df: velas OHLCV de toda la serie.

        Returns:
            Una copia de `df` con las columnas de indicador agregadas (o `df` mismo si la
            estrategia no precalcula).
        """
        return df
