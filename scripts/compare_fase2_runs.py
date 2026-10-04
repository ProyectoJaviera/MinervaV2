"""Compara dos corridas del backtest de Fase 2 guardadas en bases SQLITE distintas
(p.ej. v1 con el error de redondeo y v2 corregida) y escribe tablas en markdown:
descartes por tope de SL, veredictos lado a lado, simulacion de cartera (Monte
Carlo) y ritmo de operaciones OOS por dia.

Solo LEE: abre ambas bases en modo solo lectura (URI `mode=ro`). Nunca escribe
en ellas. Correr sobre COPIAS de la base real, como exige CLAUDE.md:

    python scripts/compare_fase2_runs.py --v1 data/backups/minerva_fase2_v1.db \
        --v2 data/backups/minerva_fase2_v2.db --out docs/FASE2_REEJECUCION_TABLAS.md
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

ALL_STRATEGIES = [
    "ema_cross_9_21",
    "trend_atr_stop_9_21_50",
    "mean_reversion_rsi14_bb20",
    "donchian_breakout_20",
    "funding_contrarian_experimental",
    "funding_contrarian_percentile_experimental",
]
# Elegibles en la cuenta real segun config (REAL_ACCOUNT_ELIGIBLE_STRATEGIES).
REAL_ACCOUNT_ELIGIBLE = [
    "ema_cross_9_21",
    "funding_contrarian_experimental",
    "funding_contrarian_percentile_experimental",
]
SL_CAP_PCT = 50.0


def _connect_ro(path: str) -> sqlite3.Connection:
    p = Path(path).resolve()
    if not p.exists():
        raise SystemExit(f"No existe la base: {p}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "n/d"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _skips_table(conn: sqlite3.Connection) -> list[str]:
    rows = []
    for strategy in ALL_STRATEGIES:
        total = conn.execute(
            "SELECT COUNT(*) FROM backtest_skipped_entries WHERE strategy = ?", (strategy,)
        ).fetchone()[0]
        # Descartes que se ENTRARIAN con la comparacion redondeada a 9 decimales:
        # son el error de coma flotante, no un riesgo real.
        recoverable = conn.execute(
            "SELECT COUNT(*) FROM backtest_skipped_entries "
            "WHERE strategy = ? AND ROUND(intended_sl_margin_loss_pct, 9) <= ?",
            (strategy, SL_CAP_PCT),
        ).fetchone()[0]
        trades = {
            seg: conn.execute(
                "SELECT COUNT(*) FROM backtest_trades WHERE strategy = ? AND segment = ?",
                (strategy, seg),
            ).fetchone()[0]
            for seg in ("IS", "OOS")
        }
        rows.append(
            f"| {strategy} | {total} | {recoverable} | {trades['IS']} | {trades['OOS']} |"
        )
    return rows


def _verdicts(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    out = {}
    for row in conn.execute("SELECT * FROM backtest_verdicts ORDER BY run_at DESC"):
        out.setdefault(row["strategy"], row)  # el veredicto mas reciente de cada estrategia
    return out


def _oos_pace(conn: sqlite3.Connection) -> tuple[int, float | None]:
    rows = conn.execute(
        "SELECT strategy, entry_time, exit_time FROM backtest_trades WHERE segment = 'OOS'"
    ).fetchall()
    eligible = [r for r in rows if r["strategy"] in REAL_ACCOUNT_ELIGIBLE]
    if not eligible:
        return 0, None
    start = min(datetime.fromisoformat(r["entry_time"]) for r in eligible)
    end = max(datetime.fromisoformat(r["exit_time"]) for r in eligible)
    days = max((end - start).total_seconds() / 86400, 1e-9)
    return len(eligible), len(eligible) / days


def _verdict_table(v1: dict, v2: dict, label: str) -> list[str]:
    lines = [
        f"| estrategia | {label} | PF OOS | PF estresado | ops totales | DD OOS % | "
        "cartera capital mediana | cartera DD mediana % | cartera MTM DD mediana % |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for strategy in ALL_STRATEGIES:
        for tag, source in (("v1", v1), ("v2", v2)):
            r = source.get(strategy)
            if r is None:
                lines.append(f"| {strategy} | {tag} | n/d | n/d | n/d | n/d | n/d | n/d | n/d |")
                continue
            lines.append(
                f"| {strategy} | {tag} | {_fmt(r['pf_oos_aggregate'])} | "
                f"{_fmt(r['pf_stressed'])} | {_fmt(r['total_trades_all_segments'], 0)} | "
                f"{_fmt(r['max_drawdown_oos_pct'], 1)} | "
                f"{_fmt(r['portfolio_final_capital_median'], 2)} | "
                f"{_fmt(r['portfolio_max_drawdown_median'], 1)} | "
                f"{_fmt(r['portfolio_mtm_max_drawdown_median'], 1)} |"
            )
    return lines


def build_report(v1_path: str, v2_path: str) -> str:
    c1, c2 = _connect_ro(v1_path), _connect_ro(v2_path)
    try:
        v1, v2 = _verdicts(c1), _verdicts(c2)
        pace1 = _oos_pace(c1)
        pace2 = _oos_pace(c2)
        lines = [
            "# Tablas de comparacion Fase 2 (v1 vs v2)",
            "",
            f"Generado: {datetime.now().isoformat(timespec='seconds')}",
            f"- v1: `{v1_path}`",
            f"- v2: `{v2_path}`",
            "",
            "## Descartes por tope de SL y operaciones por segmento",
            "",
            "| estrategia | descartes totales v2 | de ellos por coma flotante (recuperables) "
            "| IS v2 | OOS v2 |",
            "|---|---|---|---|---|",
            *_skips_table(c2),
            "",
            "Para v1 (el error), los mismos descartes se leen de la base v1:",
            "",
            "| estrategia | descartes v1 | de ellos recuperables | IS v1 | OOS v1 |",
            "|---|---|---|---|---|",
            *_skips_table(c1),
            "",
            "## Veredictos y simulacion de cartera (v1 vs v2)",
            "",
            *_verdict_table(v1, v2, "corrida"),
            "",
            "## Ritmo de operaciones OOS de las estrategias elegibles en cuenta real",
            "",
            f"- v1: {pace1[0]} operaciones OOS, ritmo {_fmt(pace1[1], 3)} por dia",
            f"- v2: {pace2[0]} operaciones OOS, ritmo {_fmt(pace2[1], 3)} por dia",
        ]
        return "\n".join(lines) + "\n"
    finally:
        c1.close()
        c2.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--v1", required=True, help="base con la corrida v1 (con el error)")
    parser.add_argument("--v2", required=True, help="base con la corrida v2 (corregida)")
    parser.add_argument("--out", help="archivo markdown de salida (por defecto, stdout)")
    args = parser.parse_args()
    report = build_report(args.v1, args.v2)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Escrito en {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
