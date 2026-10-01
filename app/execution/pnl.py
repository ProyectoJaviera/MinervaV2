"""Formulas de comisiones y PnL, compartidas por `PaperBackend` (paper
trading en vivo) y el motor de backtest (Fase 2), para que una estrategia
se comporte exactamente igual en ambos. Ver el ejemplo numerico verificado
en PROGRESS.md.

Formulas:
    notional      = margen_usdt * leverage
    qty           = notional / precio_entrada
    fee_entrada   = notional * taker_fee_pct
    fee_salida    = (qty * precio_salida) * taker_fee_pct
    pnl_bruto     = (precio_salida - precio_entrada) * qty          si LONG
    pnl_bruto     = (precio_entrada - precio_salida) * qty          si SHORT
    pnl_neto      = pnl_bruto - fee_entrada - fee_salida - otros_costos
"""

from __future__ import annotations

from dataclasses import dataclass

from app.persistence.models import Side


@dataclass(frozen=True)
class OpenFill:
    qty: float
    notional_usdt: float
    fee_entry_usdt: float


@dataclass(frozen=True)
class CloseResult:
    fee_exit_usdt: float
    pnl_gross_usdt: float
    pnl_net_usdt: float


def compute_open_fill(
    margin_usdt: float, leverage: int, entry_price: float, taker_fee_pct: float
) -> OpenFill:
    notional = margin_usdt * leverage
    qty = notional / entry_price
    fee_entry = notional * taker_fee_pct
    return OpenFill(qty=qty, notional_usdt=notional, fee_entry_usdt=fee_entry)


def compute_close_result(
    side: Side,
    qty: float,
    entry_price: float,
    exit_price: float,
    fee_entry_usdt: float,
    taker_fee_pct: float,
    extra_costs_usdt: float = 0.0,
) -> CloseResult:
    """`extra_costs_usdt` permite sumar costos adicionales ya calculados en
    USDT (slippage, funding) sin acoplar este modulo a como se calculan --
    el llamador los computa y los pasa aqui, se restan del PnL neto igual
    que las comisiones."""
    exit_notional = qty * exit_price
    fee_exit = exit_notional * taker_fee_pct

    if side == Side.LONG:
        pnl_gross = (exit_price - entry_price) * qty
    else:
        pnl_gross = (entry_price - exit_price) * qty

    pnl_net = pnl_gross - fee_entry_usdt - fee_exit - extra_costs_usdt
    return CloseResult(fee_exit_usdt=fee_exit, pnl_gross_usdt=pnl_gross, pnl_net_usdt=pnl_net)
