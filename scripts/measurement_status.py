"""CLI de solo lectura sobre el avance de la medicion en vivo (TAREA D/
Subfase C del plan pre-Fase 5 aprobado 2026-10-14) -- wrapper fino sobre
`app.trading.measurement_status` (la logica real vive ahi, compartida con
los endpoints async bajo `/api/`; ver ese modulo para el diseno completo:
por que no mira PnL, el algoritmo de conglomerados, etc.).

Solo LEE: abre la base en modo solo lectura (URI `mode=ro`, igual que
`scripts/compare_fase2_runs.py`) -- nunca escribe en ella, y por eso no
corre las migraciones de `app.persistence.database.Database` (que si
escriben). Correr sobre una COPIA de la base, como el resto de scripts que
aceptan `--db` (`scripts/llm_pilot.py`/`scripts/llm_smoke.py`): ruta
distinta de `settings.database_path`, verificada con un assert explicito.

Ejecutalo asi:

    python scripts/measurement_status.py --db data/backups/minerva_copia.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.trading.ai_value import MIN_EFFECTIVE_N  # noqa: E402
from app.trading.measurement_status import (  # noqa: E402
    APPROVAL_BAND,
    NON_OK_STATUSES,
    RATE_WINDOW_DAYS,
    MeasurementStatus,
    compute_status,
)


def _connect_ro(path: str) -> sqlite3.Connection:
    p = Path(path).resolve()
    if not p.exists():
        raise SystemExit(f"No existe la base: {p}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_days(value: float | None) -> str:
    if value is None:
        return "sin datos suficientes para estimar (poco ritmo reciente o poca historia)"
    if value == 0.0:
        return "ya alcanzado"
    return f"~{value:.1f} dias"


def format_report(status: MeasurementStatus) -> str:
    lines = [
        "=== Avance de la medicion en vivo (hash congelado) ===",
        f"PROMPT_SHA256 congelado: {status.frozen_hash}",
    ]
    if status.total_medicion_rows == 0:
        lines.append("")
        lines.append(
            "Sin filas en llm_logs con fase='MEDICION' bajo este hash todavia -- "
            "la medicion no tiene actividad registrada en esta copia."
        )
        return "\n".join(lines) + "\n"

    lines.append(f"Instante mas reciente en esta copia: {status.data_now.isoformat()}")
    lines.append(
        f"Primera fila MEDICION bajo este hash: {status.first_medicion_row_at.isoformat()}"
    )
    lines.append("")
    lines.append("-- 1) Grupos medidos con fila OK --")
    lines.append(f"APROBADA: {status.ok_by_label['APROBADA']}")
    lines.append(f"RECHAZADA: {status.ok_by_label['RECHAZADA']}")
    lines.append(f"Total OK: {status.total_ok}")
    lines.append("")
    lines.append("-- 2) Tasa de aprobacion --")
    if status.approval_rate is None:
        lines.append("Sin decisiones OK todavia: no se puede evaluar la banda.")
    else:
        lines.append(f"Tasa de APROBAR: {status.approval_rate:.1%}")
        dentro = "SI" if status.within_band else "NO"
        lines.append(f"Dentro de la banda {APPROVAL_BAND[0]:.0%}-{APPROVAL_BAND[1]:.0%}: {dentro}")
    lines.append("")
    lines.append("-- 3) Filas MEDICION no OK, por causa --")
    any_non_ok = False
    for stat in NON_OK_STATUSES:
        count = status.non_ok_counts[stat]
        if count:
            any_non_ok = True
            lines.append(f"{stat}: {count} ({status.non_ok_pct(stat):.1%} del total MEDICION)")
    if not any_non_ok:
        lines.append("ninguna")
    lines.append(f"Total de filas MEDICION (OK + no OK): {status.total_medicion_rows}")
    lines.append("")
    lines.append("-- 4) Grupos omitidos sin ninguna fila en llm_logs --")
    lines.append(
        f"{status.omitted_without_row} (sombras posteriores a la primera fila MEDICION, "
        "por demora > 1800s o hash no coincidente en ese ciclo -- separado para la seccion (m))"
    )
    lines.append("")
    lines.append("-- 5) Filas con JSON reparado --")
    lines.append(
        f"{status.json_repaired_count}/{status.total_medicion_rows} "
        f"({status.json_repaired_count / status.total_medicion_rows:.1%})"
    )
    lines.append("")
    lines.append("-- 6) Coste --")
    lines.append(
        f"Acumulado: {status.total_cost_usd:.6f} USD en {status.total_medicion_rows} filas "
        f"({status.cost_per_row_usd():.6f} USD/fila en promedio)"
    )
    lines.append("")
    lines.append(f"-- 7) Camino a MIN_EFFECTIVE_N={MIN_EFFECTIVE_N} por lado --")
    lines.append(
        f"N efectivo (conglomerados) APROBADA: {status.n_eff_approved}/{MIN_EFFECTIVE_N} "
        f"(faltan {status.shortfall('APROBADA')})"
    )
    lines.append(
        f"N efectivo (conglomerados) RECHAZADA: {status.n_eff_rejected}/{MIN_EFFECTIVE_N} "
        f"(faltan {status.shortfall('RECHAZADA')})"
    )
    lines.append(
        f"Ritmo de los ultimos {status.window_days:.1f} dias disponibles "
        f"(ventana de {RATE_WINDOW_DAYS:.0f}, o todos los disponibles si hay menos historia):"
    )
    rate_app = f"{status.rate_approved_per_day:.2f}" if status.rate_approved_per_day else "0.00"
    rate_rej = f"{status.rate_rejected_per_day:.2f}" if status.rate_rejected_per_day else "0.00"
    lines.append(f"  APROBADA: {rate_app} conglomerados efectivos/dia")
    lines.append(f"  RECHAZADA: {rate_rej} conglomerados efectivos/dia")
    if status.window_days < 1.0:
        lines.append("  (aviso: menos de 1 dia de historia -- estimacion volatil)")
    lines.append(f"Dias estimados para APROBADA: {_fmt_days(status.days_needed('APROBADA'))}")
    lines.append(f"Dias estimados para RECHAZADA: {_fmt_days(status.days_needed('RECHAZADA'))}")
    return "\n".join(lines) + "\n"


def main(db_path: str) -> None:
    conn = _connect_ro(db_path)
    try:
        status = compute_status(conn)
        print(format_report(status))
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    args = parser.parse_args()

    assert os.path.abspath(args.db) != os.path.abspath(settings.database_path), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    main(args.db)
