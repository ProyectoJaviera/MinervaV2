"""Slippage como costo plano en USDT -- la MISMA formula que el motor de backtest
(`app/backtesting/engine.py`) y el paper trading en vivo (subfase 3.4). No mueve
el precio: se resta del PnL neto, igual que las comisiones.

Entrada: `notional * bps/10000`. Salida: `qty * precio_salida * bps/10000`.
El parametro es `BACKTEST_SLIPPAGE_BPS` (ver app/config.py), el mismo que usa el
backtest, para que una estrategia pague exactamente el mismo costo en ambos.
"""

from __future__ import annotations


def entry_slippage_usdt(notional_usdt: float, slippage_bps: float) -> float:
    return notional_usdt * (slippage_bps / 10_000)


def exit_slippage_usdt(qty: float, exit_price: float, slippage_bps: float) -> float:
    return (qty * exit_price) * (slippage_bps / 10_000)
