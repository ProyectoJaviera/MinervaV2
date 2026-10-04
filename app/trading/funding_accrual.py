"""Funding acumulado de una posicion abierta (subfase 3.4), a partir de los
eventos reales de `funding_cache`. Usado por el monitor en vivo, por la
reconciliacion al reiniciar y al cerrar, para que el PnL incluya el funding y
ningun evento se cuente dos veces: `funding_last_applied_ms` guarda el ultimo
evento ya aplicado.

Convencion (igual que `app/backtesting/funding.py::funding_cost_for_bar`): una
tasa positiva hace que LONG pague a SHORT; el costo es notional * tasa, con signo
+1 para LONG y -1 para SHORT, y `funding_paid_usdt` es el total pagado (positivo
= la posicion paga).
"""

from __future__ import annotations

from app.persistence.database import Database
from app.persistence.models import Side, Trade
from app.persistence.repositories import funding_repo, trades_repo


async def accrue_funding(db: Database, trade: Trade, until_ms: int) -> Trade:
    opened_ms = int(trade.opened_at.timestamp() * 1000)
    after_ms = max(opened_ms, trade.funding_last_applied_ms or 0)
    events = await funding_repo.get_funding(db, trade.symbol, after_ms + 1, until_ms)
    if not events:
        return trade

    sign = 1.0 if trade.side == Side.LONG else -1.0
    paid = trade.funding_paid_usdt
    for _, rate in events:
        paid += trade.notional_usdt * rate * sign
    last_ms = events[-1][0]
    await trades_repo.update_funding(
        db, trade.id, paid, last_ms, trade.funding_is_approximated
    )
    return trade.model_copy(update={"funding_paid_usdt": paid, "funding_last_applied_ms": last_ms})
