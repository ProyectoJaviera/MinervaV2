"""Indicadores tecnicos calculados con pandas puro.

Se evito `pandas-ta` por decision del usuario en la Fase 0 (mantenimiento incierto).
Todos son causales: el valor de una vela depende solo de esa vela y de las anteriores,
lo que permite precalcularlos sobre toda la serie sin mirar al futuro.
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """Media movil exponencial (`adjust=False`).

    Args:
        series: serie de precios.
        span: ventana de la EMA (>= 1).

    Returns:
        La EMA, de la misma longitud que `series`.

    Raises:
        ValueError: si `span` es menor que 1.
    """
    if span < 1:
        raise ValueError("span debe ser >= 1")
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder.

    Las primeras `period` posiciones son NaN (no hay historia para suavizar). Si no hay
    perdidas y si hay ganancias vale 100; si el precio esta plano vale 50 por convencion.

    Args:
        series: serie de precios de cierre.
        period: periodo del RSI (>= 1).

    Returns:
        El RSI en la escala 0-100.

    Raises:
        ValueError: si `period` es menor que 1.
    """
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


def sma(series: pd.Series, period: int) -> pd.Series:
    """Media movil simple; las primeras `period - 1` posiciones son NaN.

    Args:
        series: serie de precios.
        period: ventana (>= 1).

    Returns:
        La media movil.

    Raises:
        ValueError: si `period` es menor que 1.
    """
    if period < 1:
        raise ValueError("period debe ser >= 1")
    return series.rolling(window=period, min_periods=period).mean()


def bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bandas de Bollinger: banda superior, media e inferior.

    La banda media es la SMA(`period`); las exteriores son la media mas o menos `num_std`
    desviaciones estandar poblacionales (`ddof=0`, la convencion de Bollinger).

    Args:
        series: serie de precios de cierre.
        period: ventana de la media y de la desviacion (>= 1).
        num_std: numero de desviaciones de las bandas exteriores.

    Returns:
        La tupla `(superior, media, inferior)`.

    Raises:
        ValueError: si `period` es menor que 1.
    """
    if period < 1:
        raise ValueError("period debe ser >= 1")
    middle = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def donchian_channel(df: pd.DataFrame, period: int = 20) -> tuple[pd.Series, pd.Series]:
    """Canal de Donchian: maximo y minimo de las ultimas `period` velas, la actual incluida.

    Args:
        df: velas con columnas `high` y `low`.
        period: ventana del canal (>= 1).

    Returns:
        La tupla `(maximo superior, minimo inferior)`.

    Raises:
        ValueError: si `period` es menor que 1 o falta `high` o `low`.
    """
    if period < 1:
        raise ValueError("period debe ser >= 1")
    for col in ("high", "low"):
        if col not in df.columns:
            raise ValueError(f"Falta la columna '{col}' en el DataFrame")
    upper = df["high"].rolling(window=period, min_periods=period).max()
    lower = df["low"].rolling(window=period, min_periods=period).min()
    return upper, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range de Wilder.

    Args:
        df: velas con columnas `high`, `low` y `close`.
        period: periodo del ATR (>= 1).

    Returns:
        El ATR en unidades de precio.

    Raises:
        ValueError: si `period` es menor que 1 o falta una columna.
    """
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
