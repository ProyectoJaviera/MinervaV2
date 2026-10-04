"""Reporte por estrategia de las operaciones sombra (subfase 3.5).

Atribucion: una operacion sombra agrupada cuenta para CADA estrategia que
coincidio en su senal, con el resultado completo. Por eso la suma de las filas
por estrategia NO es el total: el total se reporta aparte, y el agregado puede
ocultar que una sola estrategia domina el resultado -- por eso el desglose
siempre se muestra junto al total (docs/FASE3_PLAN.md, punto 2).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.persistence.database import Database
from app.persistence.models import ShadowTrade
from app.persistence.repositories import shadow_repo


@dataclass
class StrategyShadowRow:
    strategy: str
    groups: int = 0
    closed: int = 0
    wins: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    pnl_net_total: float = 0.0

    @property
    def profit_factor(self) -> float | None:
        if self.gross_loss == 0:
            return None
        return self.gross_profit / self.gross_loss

    @property
    def expectancy(self) -> float | None:
        return self.pnl_net_total / self.closed if self.closed else None

    @property
    def winrate(self) -> float | None:
        return self.wins / self.closed if self.closed else None


@dataclass
class ShadowTotals:
    groups: int
    closed: int
    open: int
    pnl_net_total: float
    by_label: dict[str, int]


async def _all_shadow(db: Database) -> tuple[list[ShadowTrade], list[ShadowTrade]]:
    return await shadow_repo.get_open(db), await shadow_repo.get_closed(db)


async def summarize_by_strategy(db: Database) -> list[StrategyShadowRow]:
    open_trades, closed_trades = await _all_shadow(db)
    rows: dict[str, StrategyShadowRow] = {}
    for trade in open_trades + closed_trades:
        for strategy in trade.contributing_strategies:
            row = rows.setdefault(strategy, StrategyShadowRow(strategy=strategy))
            row.groups += 1
    for trade in closed_trades:
        pnl = trade.pnl_net_usdt or 0.0
        for strategy in trade.contributing_strategies:
            row = rows[strategy]
            row.closed += 1
            row.pnl_net_total += pnl
            if pnl > 0:
                row.wins += 1
                row.gross_profit += pnl
            elif pnl < 0:
                row.gross_loss += -pnl
    return sorted(rows.values(), key=lambda r: r.strategy)


async def summarize_totals(db: Database) -> ShadowTotals:
    open_trades, closed_trades = await _all_shadow(db)
    by_label: dict[str, int] = {}
    for trade in open_trades + closed_trades:
        by_label[trade.llm_decision] = by_label.get(trade.llm_decision, 0) + 1
    return ShadowTotals(
        groups=len(open_trades) + len(closed_trades),
        closed=len(closed_trades),
        open=len(open_trades),
        pnl_net_total=sum(t.pnl_net_usdt or 0.0 for t in closed_trades),
        by_label=by_label,
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/d" if value is None else f"{value:.{digits}f}"


def render_markdown(rows: list[StrategyShadowRow], totals: ShadowTotals) -> str:
    lines = [
        "## Total de operaciones sombra",
        "",
        f"- Grupos (una operacion por senal agrupada): {totals.groups}",
        f"- Cerradas: {totals.closed}, abiertas: {totals.open}",
        f"- PnL neto total (sin doble conteo): {totals.pnl_net_total:.4f} USDT",
        f"- Etiquetas LLM: {totals.by_label}",
        "",
        "## Desglose por estrategia (atribucion: una operacion cuenta en cada contribuyente)",
        "",
        "| estrategia | grupos | cerradas | winrate | PF | esperanza USDT | PnL neto USDT |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.strategy} | {r.groups} | {r.closed} | {_fmt(r.winrate)} | "
            f"{_fmt(r.profit_factor)} | {_fmt(r.expectancy, 4)} | {r.pnl_net_total:.4f} |"
        )
    lines.append("")
    lines.append(
        "Las filas por estrategia no suman el total: una operacion agrupada se "
        "atribuye a cada contribuyente."
    )
    return "\n".join(lines) + "\n"
