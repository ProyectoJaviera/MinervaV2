"""Reporte de las operaciones sombra (subfase 3.5).

Atribucion (ajuste 2): una operacion sombra agrupada se acredita a CADA estrategia
contribuyente con su resultado completo, y ademas el reporte separa:

- **Grupos de una sola estrategia**: la operacion usa los niveles de esa estrategia,
  asi que es COMPARABLE con el backtest de esa estrategia.
- **Grupos mixtos**: la operacion usa los niveles de la estrategia representativa.
  Acreditar su resultado a las demas NO es comparable con el backtest de esas
  estrategias. Por eso se muestran aparte, con aviso.

Entrada de la sombra (ajuste 3): la operacion entra al PRECIO DE CIERRE de la vela
evaluada, sin demora ni slippage de llenado a mercado. La cuenta real entra al
precio de mercado con slippage. Por eso el PnL de la sombra no es comparable con el
de la cuenta real. Para comparar APROBADA con RECHAZADA dentro de la sombra no
afecta: todas entran igual.

El total no suma las filas por estrategia: una operacion mixta se cuenta en cada
contribuyente.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.persistence.database import Database
from app.persistence.models import ShadowTrade
from app.persistence.repositories import llm_logs_repo, shadow_repo
from app.trading.ai_value import AiValueVerdict, ai_value_verdict, assign_clusters


@dataclass
class StrategyShadowRow:
    strategy: str
    groups: int = 0
    closed: int = 0
    wins: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    pnl_net_total: float = 0.0
    single_closed: int = 0
    single_wins: int = 0
    single_gross_profit: float = 0.0
    single_gross_loss: float = 0.0
    single_pnl_net: float = 0.0
    mixed_closed: int = 0
    mixed_pnl_net: float = 0.0

    @staticmethod
    def _pf(gross_profit: float, gross_loss: float) -> float | None:
        return None if gross_loss == 0 else gross_profit / gross_loss

    @property
    def profit_factor(self) -> float | None:
        return self._pf(self.gross_profit, self.gross_loss)

    @property
    def single_profit_factor(self) -> float | None:
        return self._pf(self.single_gross_profit, self.single_gross_loss)

    @property
    def expectancy(self) -> float | None:
        return self.pnl_net_total / self.closed if self.closed else None

    @property
    def single_expectancy(self) -> float | None:
        return self.single_pnl_net / self.single_closed if self.single_closed else None

    @property
    def winrate(self) -> float | None:
        return self.wins / self.closed if self.closed else None

    @property
    def single_winrate(self) -> float | None:
        return self.single_wins / self.single_closed if self.single_closed else None


@dataclass
class ShadowTotals:
    groups: int
    closed: int
    open: int
    pnl_net_total: float
    by_label: dict[str, int]
    effective_closed: int
    by_label_raw: dict[str, int]
    by_label_effective: dict[str, int]


def _all(trades_open: list[ShadowTrade], trades_closed: list[ShadowTrade]) -> list[ShadowTrade]:
    return trades_open + trades_closed


def summarize_by_strategy(
    trades_open: list[ShadowTrade], trades_closed: list[ShadowTrade]
) -> list[StrategyShadowRow]:
    rows: dict[str, StrategyShadowRow] = {}
    for trade in _all(trades_open, trades_closed):
        for strategy in trade.contributing_strategies:
            row = rows.setdefault(strategy, StrategyShadowRow(strategy=strategy))
            row.groups += 1

    for trade in trades_closed:
        pnl = trade.pnl_net_usdt or 0.0
        single = len(trade.contributing_strategies) == 1
        for strategy in trade.contributing_strategies:
            row = rows[strategy]
            row.closed += 1
            row.pnl_net_total += pnl
            if pnl > 0:
                row.wins += 1
                row.gross_profit += pnl
            elif pnl < 0:
                row.gross_loss += -pnl
            if single:
                row.single_closed += 1
                row.single_pnl_net += pnl
                if pnl > 0:
                    row.single_wins += 1
                    row.single_gross_profit += pnl
                elif pnl < 0:
                    row.single_gross_loss += -pnl
            else:
                row.mixed_closed += 1
                row.mixed_pnl_net += pnl
    return sorted(rows.values(), key=lambda r: r.strategy)


def summarize_totals(
    trades_open: list[ShadowTrade], trades_closed: list[ShadowTrade]
) -> ShadowTotals:
    by_label_raw: dict[str, int] = {}
    for trade in _all(trades_open, trades_closed):
        by_label_raw[trade.llm_decision] = by_label_raw.get(trade.llm_decision, 0) + 1
    by_label_eff: dict[str, int] = {}
    assignment = assign_clusters(trades_closed)
    seen: dict[str, set[int]] = {}
    for trade in trades_closed:
        seen.setdefault(trade.llm_decision, set()).add(assignment[trade.id])
    for label, clusters in seen.items():
        by_label_eff[label] = len(clusters)
    return ShadowTotals(
        groups=len(trades_open) + len(trades_closed),
        closed=len(trades_closed),
        open=len(trades_open),
        pnl_net_total=sum(t.pnl_net_usdt or 0.0 for t in trades_closed),
        by_label=by_label_raw,
        effective_closed=len(set(assignment.values())),
        by_label_raw=by_label_raw,
        by_label_effective=by_label_eff,
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/d" if value is None else f"{value:.{digits}f}"


def _verdict_section(verdict: AiValueVerdict) -> list[str]:
    ci = "n/d" if verdict.ci_low is None else f"[{verdict.ci_low:.2%}, {verdict.ci_high:.2%}]"
    diff = "n/d" if verdict.diff is None else f"{verdict.diff:.2%}"
    sin_llm = "n/d" if verdict.sin_llm_share is None else f"{verdict.sin_llm_share:.1%}"
    marca = " (MAGNITUD_BAJA)" if verdict.magnitud_baja else ""
    return [
        "## Criterio de valor de la IA (bootstrap por conglomerados, tres veredictos)",
        "",
        f"- Aprobadas: N bruto {verdict.n_raw_approved}, N efectivo {verdict.n_eff_approved}",
        f"- Rechazadas: N bruto {verdict.n_raw_rejected}, N efectivo {verdict.n_eff_rejected}",
        f"- Diferencia de esperanza APROBADA - RECHAZADA (% del margen): {diff}, IC 95 % {ci}",
        f"- SIN_LLM: {verdict.sin_llm_count} de {verdict.total_groups} grupos ({sin_llm})",
        f"- **Veredicto: {verdict.verdict}{marca}** ({verdict.reason})",
        "",
    ]


def render_markdown(
    rows: list[StrategyShadowRow], totals: ShadowTotals, verdict: AiValueVerdict,
) -> str:
    lines = [
        "## Total de operaciones sombra",
        "",
        f"- Grupos (una operacion por senal agrupada): {totals.groups}",
        f"- Cerradas: {totals.closed} (N bruto), N efectivo por conglomerados: "
        f"{totals.effective_closed}, abiertas: {totals.open}",
        f"- PnL neto total (sin doble conteo): {totals.pnl_net_total:.4f} USDT",
        f"- Etiquetas LLM, N bruto: {totals.by_label_raw}",
        f"- Etiquetas LLM, N efectivo: {totals.by_label_effective}",
        "",
        "## Grupos de una sola estrategia (comparables con el backtest de esa estrategia)",
        "",
        "| estrategia | cerradas | winrate | PF | esperanza USDT | PnL neto USDT |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.strategy} | {r.single_closed} | {_fmt(r.single_winrate)} | "
            f"{_fmt(r.single_profit_factor)} | {_fmt(r.single_expectancy, 4)} | "
            f"{r.single_pnl_net:.4f} |"
        )
    lines += [
        "",
        "## Grupos mixtos (NO comparables con el backtest de cada estrategia)",
        "",
        "Un grupo mixto usa los niveles de la estrategia representativa. Su resultado "
        "se acredita a todas las contribuyentes, asi que no es comparable con el "
        "backtest de ninguna de ellas por separado.",
        "",
        "| estrategia | cerradas en grupos mixtos | PnL neto USDT en grupos mixtos |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.strategy} | {r.mixed_closed} | {r.mixed_pnl_net:.4f} |")
    lines += [
        "",
        "## Desglose total por estrategia (atribucion, con doble conteo)",
        "",
        "| estrategia | grupos | cerradas | winrate | PF | esperanza USDT | PnL neto USDT |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.strategy} | {r.groups} | {r.closed} | {_fmt(r.winrate)} | "
            f"{_fmt(r.profit_factor)} | {_fmt(r.expectancy, 4)} | {r.pnl_net_total:.4f} |"
        )
    lines += ["", "Las filas por estrategia no suman el total: una operacion agrupada se "
              "atribuye a cada contribuyente.", ""]
    lines += _verdict_section(verdict)
    lines += [
        "Sombra: entrada al precio de cierre de la vela evaluada, sin demora ni slippage "
        "de llenado. No comparable con la cuenta real.",
    ]
    return "\n".join(lines) + "\n"


async def build_report(db: Database) -> str:
    trades_open = await shadow_repo.get_open(db)
    trades_closed = await shadow_repo.get_closed(db)
    rows = summarize_by_strategy(trades_open, trades_closed)
    totals = summarize_totals(trades_open, trades_closed)
    piloto_keys = await llm_logs_repo.get_piloto_signal_group_keys(db)
    # El veredicto mira TODOS los grupos (abiertos y cerrados): el SIN_LLM se
    # cuenta sobre el total del periodo, no solo sobre las cerradas (seccion i/m).
    verdict = ai_value_verdict(trades_open + trades_closed, piloto_keys=piloto_keys)
    return render_markdown(rows, totals, verdict)
