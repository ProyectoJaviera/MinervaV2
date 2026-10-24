"""Estado del brazo EJECUTADO de la cuenta paper (subfase 3.9) sobre una COPIA de la base.

Solo LEE: abre la copia en modo solo lectura (`mode=ro`, como `scripts/measurement_status.py`) y
nunca escribe. `--db` es obligatorio y debe ser una ruta distinta de `settings.database_path`
(assert explicito). **Sin PnL ni resultados**: solo la decision de ejecucion -- cuantas
aprobadas se abrieron, cuantas se omitieron y por que, y la calidad de la entrada (retraso y
diferencia de precio frente a la sombra, que se miden al abrir, no al cerrar).

    python scripts/paper_execution_status.py --db <copia consistente de la base>
    python scripts/paper_execution_status.py --db <copia> --last 40

Sirve para verificar el despliegue: el brazo armado (`real_arm_started_at`), las primeras filas
`SKIPPED/RISK_STRATEGY_NOT_ELIGIBLE` (esperables: ~3 de cada 4 aprobadas) y la primera
`OPENED` con su `trade_id`.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import UTC, datetime

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from scripts.measurement_status import _connect_ro  # noqa: E402

KEY_STARTED = "real_arm_started_at"
KEY_BASELINE = "real_arm_drawdown_baseline"


def _utc(iso: str | None) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[int(round(p * (len(ordered) - 1)))]


def format_report(conn, *, last: int = 20) -> str:
    """El texto del estado del brazo ejecutado de la cuenta paper, sin PnL ni resultados.

    Muestra desde cuando esta armado (`real_arm_started_at`) y la linea base del contador de
    drawdown, cuantas decisiones APROBADA se abrieron, se omitieron (por motivo) o siguen
    pendientes, la calidad de la entrada (retraso y diferencia de precio frente a la sombra),
    los simbolos que no llegan al tamano minimo y las ultimas `last` decisiones. Una copia
    anterior a la subfase 3.9 (sin `paper_execution_log`) se informa y termina.

    Args:
        conn: conexion de solo lectura con `row_factory = sqlite3.Row`.
        last: cuantas decisiones recientes listar.

    Returns:
        El informe en texto, terminado en salto de linea.

    Efectos:
        Lee `system_state` y `paper_execution_log`.
    """
    state = dict(conn.execute("SELECT key, value FROM system_state"))
    lines = ["=== Brazo ejecutado de la cuenta paper (subfase 3.9) ==="]
    started = state.get(KEY_STARTED)
    lines.append(
        f"Armado desde: {_utc(started)}" if started
        else "Armado desde: nunca (el brazo todavia no se activo en esta copia)"
    )
    lines.append(f"Linea base del contador de drawdown: {state.get(KEY_BASELINE, '-')}")

    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'paper_execution_log'"
    ).fetchone()
    if table is None:
        lines += ["", "No existe la tabla paper_execution_log: esta copia es anterior a la 3.9."]
        return "\n".join(lines) + "\n"

    rows = conn.execute("SELECT * FROM paper_execution_log ORDER BY id").fetchall()
    outcomes = {"OPENED": 0, "SKIPPED": 0, "PENDING": 0}
    reasons: dict[str, int] = {}
    for r in rows:
        outcomes[r["outcome"]] += 1
        if r["outcome"] == "SKIPPED":
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    lines += [
        "",
        f"Decisiones APROBADA consideradas: {len(rows)}",
        f"- Abiertas (OPENED): {outcomes['OPENED']}",
        f"- Omitidas (SKIPPED): {outcomes['SKIPPED']}",
        f"- Pendientes (PENDING): {outcomes['PENDING']}"
        + ("  <- solo deberia verse un instante" if outcomes["PENDING"] else ""),
    ]
    if reasons:
        lines += ["", "Omitidas por motivo:"]
        lines += [f"- {k}: {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])]

    opened = [r for r in rows if r["outcome"] == "OPENED"]
    delays: list[float] = []
    gaps: list[float] = []
    for r in opened:
        details = json.loads(r["details_json"] or "{}")
        if details.get("entry_delay_s") is not None:
            delays.append(details["entry_delay_s"])
        if details.get("adverse_gap_bps") is not None:
            gaps.append(details["adverse_gap_bps"])
    if delays:
        lines += [
            "",
            f"Calidad de la entrada ({len(delays)} abiertas; se mide al abrir, no al cerrar):",
            f"- Retraso cierre de vela -> apertura: mediana {statistics.median(delays):.1f} s, "
            f"p95 {_pct(delays, 0.95):.1f} s, max {max(delays):.1f} s",
            f"- Diferencia de precio frente a la sombra (bps, positivo = peor): mediana "
            f"{statistics.median(gaps):.1f}, max {max(gaps):.1f}" if gaps else "- sin precios",
        ]
    below = [r for r in rows if r["reason"] == "BELOW_MIN_ORDER"]
    if below:
        lines += ["", "No ejecutables por tamano minimo (simbolo: cuantas veces):"]
        counts: dict[str, int] = {}
        for r in below:
            counts[r["symbol"]] = counts.get(r["symbol"], 0) + 1
        lines += [f"- {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]

    lines += ["", f"Ultimas {min(last, len(rows))} decisiones:"]
    for r in rows[-last:][::-1]:
        tail = f"trade {r['trade_id']}" if r["outcome"] == "OPENED" else (r["reason"] or "")
        lines.append(
            f"- {_utc(r['created_at'])} {r['symbol']} {r['side']} {r['strategy'] or '-'} "
            f"(decision a {r['decision_delay_s']:.1f} s) -> {r['outcome']} {tail}"
        )
    return "\n".join(lines) + "\n"


def main(db_path: str, *, last: int = 20) -> None:
    """Imprime el estado del brazo ejecutado de una copia, abierta en solo lectura.

    Args:
        db_path: copia de la base (nunca la de trabajo).
        last: cuantas decisiones recientes listar.

    Raises:
        SystemExit: si la base no existe (la lanza `_connect_ro`).

    Efectos:
        Abre la copia con `mode=ro` y lee lo que indica `format_report`. Imprime el informe.
    """
    conn = _connect_ro(db_path)
    try:
        print(format_report(conn, last=last))
    finally:
        conn.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--last", type=int, default=20, help="cuantas decisiones recientes listar")
    args = parser.parse_args()

    assert os.path.abspath(args.db) != os.path.abspath(settings.database_path), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    main(args.db, last=args.last)
