"""Avance de la medicion en vivo (subfase 3.6, TAREA D -- docs/FASE3_6_LLM.md
seccion k) bajo el hash de prompt CONGELADO (`app.trading.shadow_live_labeler.
FROZEN_PROMPT_SHA256`). Deliberadamente **no mira PnL ni resultados de las
operaciones** (nunca lee `pnl_net_usdt`, `pnl_gross_usdt`, `exit_price` ni
`close_reason`): solo conteos de `llm_logs` y las marcas de tiempo/lado de
`shadow_trades` necesarias para agrupar en conglomerados (mismo criterio de
`app.trading.ai_value.assign_clusters`, reimplementado aqui sobre filas
crudas para no tener que poblar un `ShadowTrade` completo solo para leer sus
campos de PnL). Mirar el PnL antes de que la medicion junte `MIN_EFFECTIVE_N`
conglomerados por lado contaminaria el veredicto final (seccion i).

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.llm.schemas import DECISION_TO_LABEL  # noqa: E402
from app.trading.ai_value import MIN_EFFECTIVE_N  # noqa: E402
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402

FASE_MEDICION = "MEDICION"
APPROVAL_BAND = (0.15, 0.85)
NON_OK_STATUSES = ("TIMEOUT", "ERROR_HTTP", "INVALID", "BUDGET_EXCEEDED")
RATE_WINDOW_DAYS = 7.0


def _connect_ro(path: str) -> sqlite3.Connection:
    p = Path(path).resolve()
    if not p.exists():
        raise SystemExit(f"No existe la base: {p}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True)
class ClosedLabeledTrade:
    """Solo lo necesario para agrupar en conglomerados (seccion i): simbolo,
    direccion, ventana de vida y la etiqueta del LLM. Deliberadamente SIN
    ningun campo de PnL."""

    symbol: str
    side: str
    opened_at: datetime
    closed_at: datetime
    llm_decision: str
    created_at: datetime


def cluster_effective_counts(trades: list[ClosedLabeledTrade]) -> tuple[int, int]:
    """N efectivo por lado: conglomerados (por simbolo+direccion, intervalos
    [apertura, cierre] que se solapan fusionados en una sola unidad) con AL
    MENOS una operacion cerrada de ese lado. Mismo algoritmo que
    `app.trading.ai_value.assign_clusters`/`build_units`, reimplementado
    sobre `ClosedLabeledTrade` (nunca lee PnL) en vez de reusar esas
    funciones (que exigen un `ShadowTrade` completo, con sus campos de PnL
    poblados) -- ver docstring del modulo."""
    groups: dict[tuple[str, str], list[ClosedLabeledTrade]] = {}
    for t in trades:
        groups.setdefault((t.symbol, t.side), []).append(t)

    n_eff_approved = 0
    n_eff_rejected = 0
    for members in groups.values():
        ordered = sorted(members, key=lambda t: t.opened_at)
        current_end: datetime | None = None
        has_approved = False
        has_rejected = False
        for t in ordered:
            if current_end is None or t.opened_at >= current_end:
                if current_end is not None:
                    n_eff_approved += 1 if has_approved else 0
                    n_eff_rejected += 1 if has_rejected else 0
                current_end = t.closed_at
                has_approved = False
                has_rejected = False
            else:
                current_end = max(current_end, t.closed_at)
            has_approved = has_approved or t.llm_decision == "APROBADA"
            has_rejected = has_rejected or t.llm_decision == "RECHAZADA"
        if current_end is not None:
            n_eff_approved += 1 if has_approved else 0
            n_eff_rejected += 1 if has_rejected else 0
    return n_eff_approved, n_eff_rejected


@dataclass(frozen=True)
class MeasurementStatus:
    frozen_hash: str
    total_medicion_rows: int = 0
    ok_by_label: dict[str, int] = field(default_factory=lambda: {"APROBADA": 0, "RECHAZADA": 0})
    approval_rate: float | None = None
    within_band: bool | None = None
    non_ok_counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(NON_OK_STATUSES, 0))
    json_repaired_count: int = 0
    total_cost_usd: float = 0.0
    omitted_without_row: int = 0
    first_medicion_row_at: datetime | None = None
    n_eff_approved: int = 0
    n_eff_rejected: int = 0
    window_days: float = 0.0
    rate_approved_per_day: float | None = None
    rate_rejected_per_day: float | None = None
    data_now: datetime | None = None

    @property
    def total_ok(self) -> int:
        return self.ok_by_label["APROBADA"] + self.ok_by_label["RECHAZADA"]

    def non_ok_pct(self, status: str) -> float | None:
        if self.total_medicion_rows == 0:
            return None
        return self.non_ok_counts[status] / self.total_medicion_rows

    def cost_per_row_usd(self) -> float | None:
        if self.total_medicion_rows == 0:
            return None
        return self.total_cost_usd / self.total_medicion_rows

    def shortfall(self, side: str) -> int:
        n_eff = self.n_eff_approved if side == "APROBADA" else self.n_eff_rejected
        return max(0, MIN_EFFECTIVE_N - n_eff)

    def days_needed(self, side: str) -> float | None:
        """`None` significa "no se puede estimar" (sin historia suficiente en
        la ventana, o ritmo reciente nulo) -- NUNCA una cantidad negativa."""
        shortfall = self.shortfall(side)
        if shortfall == 0:
            return 0.0
        rate = self.rate_approved_per_day if side == "APROBADA" else self.rate_rejected_per_day
        if not rate:  # None o 0.0: sin ritmo reciente del que proyectar
            return None
        return shortfall / rate


def compute_status(
    conn: sqlite3.Connection,
    *,
    frozen_hash: str = FROZEN_PROMPT_SHA256,
    rate_window_days: float = RATE_WINDOW_DAYS,
) -> MeasurementStatus:
    rows = conn.execute(
        "SELECT status, decision, json_repaired, cost_usd, created_at, signal_group_key "
        "FROM llm_logs WHERE fase = ? AND prompt_sha256 = ?",
        (FASE_MEDICION, frozen_hash),
    ).fetchall()
    if not rows:
        return MeasurementStatus(frozen_hash=frozen_hash)

    total_medicion_rows = len(rows)
    ok_by_label = {"APROBADA": 0, "RECHAZADA": 0}
    non_ok_counts = dict.fromkeys(NON_OK_STATUSES, 0)
    for r in rows:
        if r["status"] == "OK":
            label = DECISION_TO_LABEL.get(r["decision"])
            if label is not None:
                ok_by_label[label] += 1
        elif r["status"] in non_ok_counts:
            non_ok_counts[r["status"]] += 1
    total_ok = ok_by_label["APROBADA"] + ok_by_label["RECHAZADA"]
    approval_rate = ok_by_label["APROBADA"] / total_ok if total_ok else None
    within_band = (
        APPROVAL_BAND[0] <= approval_rate <= APPROVAL_BAND[1] if approval_rate is not None else None
    )
    json_repaired_count = sum(1 for r in rows if r["json_repaired"])
    total_cost_usd = sum(r["cost_usd"] or 0.0 for r in rows)
    created_ats = [datetime.fromisoformat(r["created_at"]) for r in rows]
    first_medicion_row_at = min(created_ats)
    data_now = max(created_ats)

    # Punto 4: sombras posteriores a la primera fila MEDICION cuyo
    # signal_group_key NUNCA tiene fila en llm_logs (bajo NINGUNA fase/hash --
    # "nunca se intento" es lo que importa aqui, no "se intento con otro
    # prompt"), por demora > 1800 s o por un hash que no coincidia en ese
    # ciclo. Separado a proposito del resto (ver seccion m).
    known_keys = {
        r["signal_group_key"] for r in conn.execute("SELECT signal_group_key FROM llm_logs")
    }
    shadow_rows = conn.execute(
        "SELECT signal_group_key, candle_close_time FROM shadow_trades WHERE candle_close_time > ?",
        (first_medicion_row_at.isoformat(),),
    ).fetchall()
    omitted_without_row = sum(
        1 for r in shadow_rows if r["signal_group_key"] not in known_keys
    )

    labeled_rows = conn.execute(
        """
        SELECT st.symbol, st.side, st.opened_at, st.closed_at, st.llm_decision, l.created_at
        FROM llm_logs l
        JOIN shadow_trades st ON st.signal_group_key = l.signal_group_key
        WHERE l.fase = ? AND l.prompt_sha256 = ? AND l.status = 'OK'
        """,
        (FASE_MEDICION, frozen_hash),
    ).fetchall()
    closed_labeled = [
        ClosedLabeledTrade(
            symbol=r["symbol"], side=r["side"],
            opened_at=datetime.fromisoformat(r["opened_at"]),
            closed_at=datetime.fromisoformat(r["closed_at"]),
            llm_decision=r["llm_decision"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in labeled_rows
        if r["closed_at"] is not None
    ]
    n_eff_approved, n_eff_rejected = cluster_effective_counts(closed_labeled)

    # Ritmo de los ultimos RATE_WINDOW_DAYS dias (o todos los disponibles si
    # hay menos), anclado al dato MAS RECIENTE de la propia copia (no al
    # reloj de pared): esta base puede ser un respaldo de hace horas o dias,
    # y anclar al reloj real subestimaria el ritmo con un hueco artificial.
    window_start = max(data_now - timedelta(days=rate_window_days), first_medicion_row_at)
    window_days = (data_now - window_start).total_seconds() / 86400.0
    window_trades = [t for t in closed_labeled if t.created_at >= window_start]
    n_eff_approved_window, n_eff_rejected_window = cluster_effective_counts(window_trades)
    rate_approved = n_eff_approved_window / window_days if window_days > 0 else None
    rate_rejected = n_eff_rejected_window / window_days if window_days > 0 else None

    return MeasurementStatus(
        frozen_hash=frozen_hash, total_medicion_rows=total_medicion_rows,
        ok_by_label=ok_by_label, approval_rate=approval_rate, within_band=within_band,
        non_ok_counts=non_ok_counts, json_repaired_count=json_repaired_count,
        total_cost_usd=total_cost_usd, omitted_without_row=omitted_without_row,
        first_medicion_row_at=first_medicion_row_at, n_eff_approved=n_eff_approved,
        n_eff_rejected=n_eff_rejected, window_days=window_days,
        rate_approved_per_day=rate_approved, rate_rejected_per_day=rate_rejected,
        data_now=data_now,
    )


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
