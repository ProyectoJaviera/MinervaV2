"""Indicadores tecnicos calculados con pandas puro (sin pandas-ta, por decision
del usuario en Fase 0: la libreria tiene soporte de mantenimiento incierto)."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """Media movil exponencial. `span` debe ser >= 1."""
    if span < 1:
        raise ValueError("span debe ser >= 1")
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder. Las primeras `period` posiciones son NaN (no hay suficiente
    historia para suavizar)."""
    if period < 1:
        raise ValueError("period debe ser >= 1")
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    result = 100 - (100 / (1 + rs))
    # Cuando avg_loss es 0 y avg_gain > 0, RSI = 100 (no hay perdidas que dividir).
    result = result.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    # Cuando ambos son 0 (precio plano), RSI = 50 por convencion (sin momentum).
    result = result.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return result


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range de Wilder. Requiere columnas 'high', 'low', 'close'."""
    if period < 1:
        raise ValueError("period debe ser >= 1")
    for col in ("high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Falta la columna '{col}' en el DataFrame")
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
