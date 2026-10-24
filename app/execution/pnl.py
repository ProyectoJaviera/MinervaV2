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
    """Resultado de abrir una posicion: cantidad, nocional y comision de entrada."""
    qty: float
    notional_usdt: float
    fee_entry_usdt: float


@dataclass(frozen=True)
class CloseResult:
    """Resultado de cerrar una posicion: comision de salida, PnL bruto y PnL neto."""
    fee_exit_usdt: float
    pnl_gross_usdt: float
    pnl_net_usdt: float


def compute_open_fill(
    margin_usdt: float, leverage: int, entry_price: float, taker_fee_pct: float
) -> OpenFill:
    """Cantidad, nocional y comision de entrada de una apertura.

    Se usa tanto en la cuenta paper en vivo como en el backtest, para que una estrategia se
    comporte igual en ambos. `nocional = margen * leverage`, `qty = nocional / precio` y
    `comision = nocional * taker_fee_pct`.

    Args:
        margin_usdt: margen, en USDT.
        leverage: apalancamiento.
        entry_price: precio de entrada.
        taker_fee_pct: comision taker como fraccion (0.0006 = 0.06 %).

    Returns:
        El `OpenFill`.
    """
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
    """Comision de salida y PnL bruto y neto de un cierre.

    `pnl_neto = pnl_bruto - comision de entrada - comision de salida - costos extra`.

    Args:
        side: direccion de la posicion.
        qty: cantidad.
        entry_price: precio de entrada.
        exit_price: precio de salida.
        fee_entry_usdt: comision de entrada ya pagada.
        taker_fee_pct: comision taker como fraccion.
        extra_costs_usdt: costos adicionales ya calculados (slippage, funding), que se
            restan igual que las comisiones sin acoplar este modulo a como se calculan.

    Returns:
        El `CloseResult`.
    """
    exit_notional = qty * exit_price
    fee_exit = exit_notional * taker_fee_pct

    if side == Side.LONG:
        pnl_gross = (exit_price - entry_price) * qty
    else:
        pnl_gross = (entry_price - exit_price) * qty

    pnl_net = pnl_gross - fee_entry_usdt - fee_exit - extra_costs_usdt
    return CloseResult(fee_exit_usdt=fee_exit, pnl_gross_usdt=pnl_gross, pnl_net_usdt=pnl_net)
