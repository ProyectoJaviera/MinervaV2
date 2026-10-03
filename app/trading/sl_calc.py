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
    """SL/TP de respaldo (precio absoluto) para una estrategia que no
    implementa `stop_price`/`take_profit_price` -- `Settings.
    backtest_fallback_sl_pct`/`backtest_fallback_tp_pct` (FRACCION 0-1 de
    movimiento de precio, ver `app/config.py`)."""
    sl_pct = settings.backtest_fallback_sl_pct
    tp_pct = settings.backtest_fallback_tp_pct
    if side == Signal.LONG:
        return entry_price * (1 - sl_pct), entry_price * (1 + tp_pct)
    return entry_price * (1 + sl_pct), entry_price * (1 - tp_pct)


def margin_loss_pct(entry_price: float, sl_price: float, leverage: int) -> float:
    """Riesgo planeado al abrir, como % del MARGEN (no del precio) --
    `(distancia_precio / precio_entrada) * leverage * 100`. Misma formula
    que compara `Settings.max_sl_margin_loss_pct` (backtest) y
    `Settings.live_sl_margin_cap_pct` (motor de riesgo en vivo): PORCENTAJE
    0-100, no fraccion."""
    price_distance = abs(entry_price - sl_price)
    return (price_distance / entry_price) * leverage * 100
