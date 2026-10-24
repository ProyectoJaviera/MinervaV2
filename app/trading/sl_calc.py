"""Calculo de SL/TP de respaldo y riesgo planeado (% de margen) -- extraido
de `app/backtesting/engine.py` (Fase 3, subfase 3.3) para que el generador
de senales en vivo (`app/trading/signal_generator.py`) use EXACTAMENTE la
misma formula que el backtest, en vez de duplicarla. Extraccion pura: el
backtest sigue dando los mismos numeros (verificado porque sus tests
existentes pasan sin tocarlos).
"""

from __future__ import annotations

from app.config import Settings
from app.strategies.base import Signal


def fallback_sl_tp_prices(
    side: Signal, entry_price: float, settings: Settings
) -> tuple[float, float]:
    """SL y TP de respaldo (precios absolutos) para una estrategia que no los define.

    Usa `BACKTEST_FALLBACK_SL_PCT` y `BACKTEST_FALLBACK_TP_PCT` (fracciones 0-1 de
    movimiento de precio, no de margen).

    Args:
        side: direccion de la entrada.
        entry_price: precio de entrada.
        settings: configuracion.

    Returns:
        La tupla `(sl, tp)`.
    """
    sl_pct = settings.backtest_fallback_sl_pct
    tp_pct = settings.backtest_fallback_tp_pct
    if side == Signal.LONG:
        return entry_price * (1 - sl_pct), entry_price * (1 + tp_pct)
    return entry_price * (1 + sl_pct), entry_price * (1 - tp_pct)


def margin_loss_pct(entry_price: float, sl_price: float, leverage: int) -> float:
    """Riesgo planeado al abrir, como porcentaje del MARGEN (no del precio).

    `(distancia al SL / precio de entrada) * leverage * 100`. Es la misma formula que
    comparan `MAX_SL_MARGIN_LOSS_PCT` (backtest) y `LIVE_SL_MARGIN_CAP_PCT` (riesgo en
    vivo): porcentaje 0-100, no fraccion. Se redondea a 9 decimales porque el SL de
    respaldo (5 % a 10x) cae EXACTAMENTE en el tope de 50 % y sin redondear la coma
    flotante da 50.00000000000001 en ~77 % de los precios, lo que la comparacion `>`
    descartaba al azar segun el precio.

    Args:
        entry_price: precio de entrada.
        sl_price: precio del SL.
        leverage: apalancamiento.

    Returns:
        El porcentaje del margen que se perderia al tocar el SL.
    """
    price_distance = abs(entry_price - sl_price)
    return round((price_distance / entry_price) * leverage * 100, 9)
