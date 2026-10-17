"""Avance de la medicion en vivo (subfase 3.6, TAREA D/Subfase C del plan
pre-Fase 5 aprobado 2026-10-14) bajo el hash de prompt CONGELADO
(`app.trading.shadow_live_labeler.FROZEN_PROMPT_SHA256`). Deliberadamente
**no mira PnL ni resultados de las operaciones** (nunca lee `pnl_net_usdt`,
`pnl_gross_usdt`, `exit_price` ni `close_reason`): solo conteos de
`llm_logs` y las marcas de tiempo/lado de `shadow_trades` necesarias para
agrupar en conglomerados (mismo criterio de
`app.trading.ai_value.assign_clusters`, reimplementado aqui sobre filas
crudas para no tener que poblar un `ShadowTrade` completo solo para leer sus
campos de PnL). Mirar el PnL antes de que la medicion junte
`MIN_EFFECTIVE_N` conglomerados por lado contaminaria el veredicto final
(seccion i).

`compute_status` (sincrono, sobre un `sqlite3.Connection` en modo solo
lectura) es el que usa `scripts/measurement_status.py` para una copia
offline de la base; `compute_status_async` (sobre la `Database` async ya
conectada del bot) es el que usan los endpoints de solo lectura bajo `/api/`
(Subfase C) -- MISMA logica, solo cambia `conn.execute(...).fetchall()` por
`await db.fetch_all(...)` (`aiosqlite.Row` soporta el mismo acceso
`row["col"]` que `sqlite3.Row`).

`is_measurement_gate_open` es el UNICO lugar que decide si la medicion ya
junto `MIN_EFFECTIVE_N` por lado. Pero abrir el gate NO basta para mostrar
resultados: `is_outcome_reveal_allowed` (gate abierto Y registro oficial del
veredicto en `docs/`, ver `app.trading.verdict_record`) es la condicion unica
que usan la API, el dashboard y los reportes para decidir si una respuesta puede
revelar PnL de sombras etiquetadas (regla del 2026-10-17: primero el placebo
oficial y el veredicto)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.llm.schemas import DECISION_TO_LABEL
from app.persistence.database import Database
from app.trading import verdict_record
from app.trading.ai_value import MIN_EFFECTIVE_N
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256

FASE_MEDICION = "MEDICION"
APPROVAL_BAND = (0.15, 0.85)
NON_OK_STATUSES = ("TIMEOUT", "ERROR_HTTP", "INVALID", "BUDGET_EXCEEDED")
RATE_WINDOW_DAYS = 7.0


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


def is_measurement_gate_open(status: MeasurementStatus) -> bool:
    """UNICA fuente de verdad sobre si la medicion ya junto `MIN_EFFECTIVE_N`
    conglomerados por lado -- `app/api/serializers.py` la usa para decidir si
    una respuesta puede revelar PnL/resultados de sombras etiquetadas."""
    return status.n_eff_approved >= MIN_EFFECTIVE_N and status.n_eff_rejected >= MIN_EFFECTIVE_N


def is_outcome_reveal_allowed(status: MeasurementStatus, docs_dir: Path | None = None) -> bool:
    """CONDICION UNICA para mostrar resultados (PnL, salida, motivo de cierre) de
    las sombras etiquetadas, en la API, el dashboard y los scripts de reporte:
    el gate abierto Y un registro oficial del veredicto en `docs/`
    (`app.trading.verdict_record`). Con el gate abierto y sin registro todavia
    hay que seguir `docs/VEREDICTO_3_6.md` (copia, placebo oficial, veredicto): si
    el placebo sale NO INFORMATIVO se esperan 7 dias y en ese tiempo NO se ven
    resultados (regla del 2026-10-17). Un registro sin gate abierto tampoco basta."""
    return is_measurement_gate_open(status) and (
        verdict_record.find_verdict_record(docs_dir) is not None
    )


def _build_status_from_rows(
    *, frozen_hash: str, rate_window_days: float, rows, known_keys, shadow_rows, labeled_rows,
) -> MeasurementStatus:
    """Computo puro, comun a `compute_status`/`compute_status_async`: ya
    recibe las 4 consultas resueltas (filas de `llm_logs`, claves conocidas,
    sombras posteriores a la primera fila, y las etiquetadas con su sombra
    unida) -- separado para no duplicar esta logica entre la variante
    sincrona y la async."""
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

    omitted_without_row = sum(
        1 for r in shadow_rows if r["signal_group_key"] not in known_keys
    )

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
    # hay menos), anclado al dato MAS RECIENTE de la propia copia/base (no al
    # reloj de pared): una copia offline puede ser de hace horas o dias, y
    # anclar al reloj real subestimaria el ritmo con un hueco artificial.
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


def compute_status(
    conn: sqlite3.Connection,
    *,
    frozen_hash: str = FROZEN_PROMPT_SHA256,
    rate_window_days: float = RATE_WINDOW_DAYS,
) -> MeasurementStatus:
    """Variante SINCRONA, sobre un `sqlite3.Connection` en modo solo lectura
    -- la usa `scripts/measurement_status.py` contra una copia offline."""
    rows = conn.execute(
        "SELECT status, decision, json_repaired, cost_usd, created_at, signal_group_key "
        "FROM llm_logs WHERE fase = ? AND prompt_sha256 = ?",
        (FASE_MEDICION, frozen_hash),
    ).fetchall()
    if not rows:
        return MeasurementStatus(frozen_hash=frozen_hash)

    first_medicion_row_at = min(datetime.fromisoformat(r["created_at"]) for r in rows)
    # Punto 4 (seccion m): sombras posteriores a la primera fila MEDICION
    # cuyo signal_group_key NUNCA tiene fila en llm_logs (bajo NINGUNA
    # fase/hash -- "nunca se intento" es lo que importa, no "con otro
    # prompt"), por demora > 1800s o por un hash que no coincidia en ese
    # ciclo.
    known_keys = {
        r["signal_group_key"] for r in conn.execute("SELECT signal_group_key FROM llm_logs")
    }
    shadow_rows = conn.execute(
        "SELECT signal_group_key, candle_close_time FROM shadow_trades WHERE candle_close_time > ?",
        (first_medicion_row_at.isoformat(),),
    ).fetchall()
    labeled_rows = conn.execute(
        """
        SELECT st.symbol, st.side, st.opened_at, st.closed_at, st.llm_decision, l.created_at
        FROM llm_logs l
        JOIN shadow_trades st ON st.signal_group_key = l.signal_group_key
        WHERE l.fase = ? AND l.prompt_sha256 = ? AND l.status = 'OK'
        """,
        (FASE_MEDICION, frozen_hash),
    ).fetchall()

    return _build_status_from_rows(
        frozen_hash=frozen_hash, rate_window_days=rate_window_days, rows=rows,
        known_keys=known_keys, shadow_rows=shadow_rows, labeled_rows=labeled_rows,
    )


async def compute_status_async(
    db: Database,
    *,
    frozen_hash: str = FROZEN_PROMPT_SHA256,
    rate_window_days: float = RATE_WINDOW_DAYS,
) -> MeasurementStatus:
    """Variante ASYNC, sobre la `Database` del bot ya conectada -- la usan
    los endpoints de solo lectura bajo `/api/` (Subfase C). MISMA logica que
    `compute_status`, solo cambia el estilo de llamada a la base."""
    rows = await db.fetch_all(
        "SELECT status, decision, json_repaired, cost_usd, created_at, signal_group_key "
        "FROM llm_logs WHERE fase = ? AND prompt_sha256 = ?",
        (FASE_MEDICION, frozen_hash),
    )
    if not rows:
        return MeasurementStatus(frozen_hash=frozen_hash)

    first_medicion_row_at = min(datetime.fromisoformat(r["created_at"]) for r in rows)
    known_rows = await db.fetch_all("SELECT signal_group_key FROM llm_logs")
    known_keys = {r["signal_group_key"] for r in known_rows}
    shadow_rows = await db.fetch_all(
        "SELECT signal_group_key, candle_close_time FROM shadow_trades WHERE candle_close_time > ?",
        (first_medicion_row_at.isoformat(),),
    )
    labeled_rows = await db.fetch_all(
        """
        SELECT st.symbol, st.side, st.opened_at, st.closed_at, st.llm_decision, l.created_at
        FROM llm_logs l
        JOIN shadow_trades st ON st.signal_group_key = l.signal_group_key
        WHERE l.fase = ? AND l.prompt_sha256 = ? AND l.status = 'OK'
        """,
        (FASE_MEDICION, frozen_hash),
    )

    return _build_status_from_rows(
        frozen_hash=frozen_hash, rate_window_days=rate_window_days, rows=rows,
        known_keys=known_keys, shadow_rows=shadow_rows, labeled_rows=labeled_rows,
    )
