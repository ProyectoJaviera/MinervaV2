"""Liquidacion de un cierre con la MISMA formula para la cuenta paper y las
operaciones sombra (subfase 3.5): comisiones, slippage plano y funding acumulado.
Compartida para que una senal tenga exactamente el mismo PnL en ambos casos."""

from __future__ import annotations

from dataclasses import dataclass

from app.execution.pnl import compute_close_result
from app.persistence.models import Side
from app.trading.slippage import exit_slippage_usdt


@dataclass(frozen=True)
class Settlement:
    fee_exit_usdt: float
    slippage_exit_usdt: float
    pnl_gross_usdt: float
    pnl_net_usdt: float


def settle_close(
    side: Side,
    qty: float,
    entry_price: float,
    exit_price: float,
    fee_entry_usdt: float,
    slippage_entry_usdt: float,
    funding_paid_usdt: float,
    taker_fee_pct: float,
    slippage_bps: float,
) -> Settlement:
    result = compute_close_result(
        side, qty, entry_price, exit_price, fee_entry_usdt, taker_fee_pct,
        extra_costs_usdt=funding_paid_usdt + slippage_entry_usdt,
    )
    slippage_exit = exit_slippage_usdt(qty, exit_price, slippage_bps)
    return Settlement(
        fee_exit_usdt=result.fee_exit_usdt,
        slippage_exit_usdt=slippage_exit,
        pnl_gross_usdt=result.pnl_gross_usdt,
        pnl_net_usdt=result.pnl_net_usdt - slippage_exit,
    )
