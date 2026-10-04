"""Funding acumulado de una posicion (real o sombra) a partir de los eventos de
`funding_cache`. Usado por el monitor en vivo, la reconciliacion al reiniciar y
el cierre, para que el PnL incluya el funding y ningun evento se cuente dos
veces: `funding_last_applied_ms` guarda el ultimo evento ya aplicado.

Convencion (igual que `app/backtesting/funding.py::funding_cost_for_bar`): una
tasa positiva hace que LONG pague a SHORT; el costo es notional * tasa, con signo
+1 para LONG y -1 para SHORT, y `funding_paid_usdt` es el total pagado (positivo
= la posicion paga).
"""

from __future__ import annotations

from app.persistence.database import Database
from app.persistence.models import Side, Trade
from app.persistence.repositories import funding_repo, trades_repo


async def compute_funding_accrual(
    db: Database, trade: Trade, until_ms: int
) -> tuple[float, int] | None:
    """(funding_pagado_nuevo, ultimo_evento_aplicado_ms) o None si no hay eventos
    nuevos en (ultimo_aplicado, until_ms]. No escribe nada."""
    opened_ms = int(trade.opened_at.timestamp() * 1000)
    after_ms = max(opened_ms, trade.funding_last_applied_ms or 0)
    events = await funding_repo.get_funding(db, trade.symbol, after_ms + 1, until_ms)
    if not events:
        return None
    sign = 1.0 if trade.side == Side.LONG else -1.0
    paid = trade.funding_paid_usdt
    for _, rate in events:
        paid += trade.notional_usdt * rate * sign
    return paid, events[-1][0]


async def accrue_funding(db: Database, trade: Trade, until_ms: int) -> Trade:
    accrual = await compute_funding_accrual(db, trade, until_ms)
    if accrual is None:
        return trade
    paid, last_ms = accrual
    await trades_repo.update_funding(
        db, trade.id, paid, last_ms, trade.funding_is_approximated
    )
    return trade.model_copy(update={"funding_paid_usdt": paid, "funding_last_applied_ms": last_ms})
