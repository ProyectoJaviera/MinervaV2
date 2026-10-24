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
oficial y el veredicto).

**Ritmo sobre tiempo EN MARCHA** (ademas del de calendario): el ritmo de calendario
divide por todos los dias de la ventana, incluidos los que el bot estuvo apagado
(p.ej. las ~58 h del 15 al 17 de octubre), asi que subestima lo que hace el bot cuando
corre. El tiempo en marcha sale de las marcas de tiempo de las evaluaciones de cada
ciclo (`signals.evaluated_at`, una por (simbolo, estrategia, timeframe, vela) y, como
hay una estrategia de 1 h, al menos una por hora con el bot evaluando): un hueco de
mas de `OFFLINE_GAP_HOURS` entre marcas consecutivas cuenta ENTERO como apagado. Solo
marcas de tiempo, nunca PnL. Si la copia no tiene ninguna marca (base anterior a la
3.3, o sintetica), el ritmo en marcha es `None` ("sin datos"), no 0 %."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
OFFLINE_GAP_HOURS = 2.0

# Marcas de evaluacion con resolucion de MINUTO (`YYYY-MM-DDTHH:MM`, UTC: `evaluated_at`
# se escribe con `datetime.now(UTC)`): basta para un umbral de horas y una rafaga de
# ~60 filas de un mismo ciclo colapsa en 1-3 marcas. Solo se leen las posteriores a
# `lower`, que es el inicio de la medicion MENOS el umbral: una marca anterior a eso
# habria dejado un hueco > umbral hasta la primera marca leida, que ya cuenta apagado.
EVAL_MARKS_SQL = (
    "SELECT DISTINCT substr(evaluated_at, 1, 16) AS m FROM signals WHERE evaluated_at >= ?"
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def eval_marks_lower_bound(first_medicion_row_at: datetime) -> str:
    """Cota inferior (minuto, UTC) de las marcas de evaluacion que hace falta leer.

    Es el inicio de la medicion MENOS el umbral de apagado: una marca anterior habria dejado
    un hueco mayor que el umbral hasta la primera marca leida.

    Args:
        first_medicion_row_at: hora de la primera fila de medicion.

    Returns:
        El texto `YYYY-MM-DDTHH:MM`.
    """
    start = _as_utc(first_medicion_row_at) - timedelta(hours=OFFLINE_GAP_HOURS)
    return start.strftime("%Y-%m-%dT%H:%M")


def parse_eval_marks(rows: Iterable) -> list[datetime]:
    """Convierte las marcas `YYYY-MM-DDTHH:MM` de la base en instantes UTC.

    Args:
        rows: filas con la columna `m`.

    Returns:
        Lista de `datetime` UTC (se ignoran las filas vacias).
    """
    return [
        datetime.strptime(r["m"], "%Y-%m-%dT%H:%M").replace(tzinfo=UTC) for r in rows if r["m"]
    ]


def running_seconds(
    marks: Iterable[datetime], window_start: datetime, window_end: datetime,
    *, gap_hours: float = OFFLINE_GAP_HOURS,
) -> float:
    """Segundos EN MARCHA dentro de una ventana, segun las marcas de evaluacion de los ciclos.

    Cada par de marcas consecutivas separadas por `gap_hours` o menos cuenta completo; un
    hueco mayor cuenta ENTERO como apagado. El extremo final de la ventana funciona como una
    marca mas (si la ultima evaluacion fue hace `gap_hours` o menos, el bot sigue en
    marcha); el comienzo anterior a la primera marca cuenta apagado.

    Args:
        marks: instantes de evaluacion (`signals.evaluated_at`, a resolucion de minuto).
        window_start: inicio de la ventana.
        window_end: fin de la ventana.
        gap_hours: hueco maximo que sigue contando como en marcha.

    Returns:
        Los segundos en marcha.
    """
    start, end = _as_utc(window_start), _as_utc(window_end)
    if end <= start:
        return 0.0
    points = sorted(p for p in (_as_utc(m) for m in marks) if p <= end)
    if not points:
        return 0.0
    if points[-1] < end:
        points.append(end)
    gap = timedelta(hours=gap_hours)
    running = 0.0
    for prev, cur in zip(points, points[1:], strict=False):
        if cur - prev > gap:
            continue
        lo, hi = max(prev, start), min(cur, end)
        if hi > lo:
            running += (hi - lo).total_seconds()
    return running


@dataclass(frozen=True)
class ClosedLabeledTrade:
    """Lo necesario para agrupar en conglomerados: simbolo, lado, vida y etiqueta del LLM.

    Deliberadamente SIN ningun campo de PnL: el avance de la medicion jamas debe mirar
    resultados.
    """

    symbol: str
    side: str
    opened_at: datetime
    closed_at: datetime
    llm_decision: str
    created_at: datetime


def cluster_effective_counts(trades: list[ClosedLabeledTrade]) -> tuple[int, int]:
    """N efectivo por lado: conglomerados con al menos una operacion cerrada de ese lado.

    Un conglomerado es un conjunto de operaciones del mismo simbolo y direccion cuyos
    intervalos `[apertura, cierre]` se solapan. Mismo algoritmo que
    `ai_value.assign_clusters`, reimplementado sobre `ClosedLabeledTrade` para no leer PnL.

    Args:
        trades: operaciones cerradas y etiquetadas.

    Returns:
        La tupla `(conglomerados con APROBADA, conglomerados con RECHAZADA)`.
    """
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
    """Avance de la medicion bajo el hash congelado: conteos, ritmo y gasto. NUNCA resultados.

    Los porcentajes de tiempo en marcha son fracciones 0-1. `None` en un ritmo significa
    "sin datos para estimar".
    """
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
    # Ritmo sobre tiempo EN MARCHA (ver el docstring del modulo). `None` = sin marcas de
    # evaluacion en la copia. Los porcentajes son fracciones 0-1.
    running_window_days: float | None = None
    uptime_pct_window: float | None = None
    uptime_pct_total: float | None = None
    rate_approved_running_per_day: float | None = None
    rate_rejected_running_per_day: float | None = None

    @property
    def total_ok(self) -> int:
        """Filas de medicion con decision valida (APROBADA mas RECHAZADA)."""
        return self.ok_by_label["APROBADA"] + self.ok_by_label["RECHAZADA"]

    def non_ok_pct(self, status: str) -> float | None:
        """Fraccion de las filas de medicion que terminaron en un estado concreto distinto de OK.

        Args:
            status: `TIMEOUT`, `ERROR_HTTP`, `INVALID` o `BUDGET_EXCEEDED`.

        Returns:
            La fraccion 0-1, o `None` si todavia no hay filas.
        """
        if self.total_medicion_rows == 0:
            return None
        return self.non_ok_counts[status] / self.total_medicion_rows

    def cost_per_row_usd(self) -> float | None:
        """Costo medio por fila de medicion.

        Returns:
            USD por fila, o `None` si todavia no hay filas.
        """
        if self.total_medicion_rows == 0:
            return None
        return self.total_cost_usd / self.total_medicion_rows

    def shortfall(self, side: str) -> int:
        """Cuantos conglomerados efectivos faltan para el punto de analisis en un lado.

        Args:
            side: `APROBADA` o `RECHAZADA`.

        Returns:
            `MIN_EFFECTIVE_N` menos el N efectivo actual (nunca negativo).
        """
        n_eff = self.n_eff_approved if side == "APROBADA" else self.n_eff_rejected
        return max(0, MIN_EFFECTIVE_N - n_eff)

    def days_needed(self, side: str) -> float | None:
        """Dias de CALENDARIO estimados para completar un lado.

        El ritmo incluye los dias en que el bot estuvo apagado. Nunca devuelve una cantidad
        negativa.

        Args:
            side: `APROBADA` o `RECHAZADA`.

        Returns:
            Los dias, 0.0 si ya se alcanzo, o `None` si no se puede estimar (sin historia en
            la ventana o ritmo nulo).
        """
        rate = self.rate_approved_per_day if side == "APROBADA" else self.rate_rejected_per_day
        return self._days(side, rate)

    def days_needed_running(self, side: str) -> float | None:
        """Dias EN MARCHA estimados para completar un lado, con el ritmo del tiempo evaluando.

        Equivale a dias de calendario solo si el bot no vuelve a apagarse.

        Args:
            side: `APROBADA` o `RECHAZADA`.

        Returns:
            Los dias, 0.0 si ya se alcanzo, o `None` si no se puede estimar.
        """
        rate = (
            self.rate_approved_running_per_day if side == "APROBADA"
            else self.rate_rejected_running_per_day
        )
        return self._days(side, rate)

    def _days(self, side: str, rate: float | None) -> float | None:
        shortfall = self.shortfall(side)
        if shortfall == 0:
            return 0.0
        if not rate:  # None o 0.0: sin ritmo reciente del que proyectar
            return None
        return shortfall / rate


def _fmt_pace_days(value: float | None, unit: str) -> str:
    if value is None:
        return "sin datos para estimar"
    if value == 0.0:
        return "ya alcanzado"
    return f"~{value:.1f} {unit}"


def _fmt_rate(rate: float | None, suffix: str) -> str:
    return "n/d" if rate is None else f"{rate:.2f}{suffix}"


def pace_lines(status: MeasurementStatus) -> list[str]:
    """Ritmo y dias estimados (calendario y en marcha), con rotulos explicitos. Sin PnL.

    Es el texto comun de `scripts/measurement_status.py`, `/metrics` de Telegram y el
    resumen diario.

    Args:
        status: el avance de la medicion.

    Returns:
        Las lineas de texto.
    """
    window = f"{status.window_days:.1f}"
    lines = [
        f"Ritmo por CALENDARIO (ultimos {window} dias, INCLUYE el tiempo con el bot apagado): "
        f"APROBADA {_fmt_rate(status.rate_approved_per_day, '/dia')} -> "
        f"{_fmt_pace_days(status.days_needed('APROBADA'), 'dias de calendario')}; "
        f"RECHAZADA {_fmt_rate(status.rate_rejected_per_day, '/dia')} -> "
        f"{_fmt_pace_days(status.days_needed('RECHAZADA'), 'dias de calendario')}"
    ]
    if status.running_window_days is None:
        lines.append(
            "Ritmo EN MARCHA: sin marcas de evaluacion de senales en esta base, no se puede "
            "calcular"
        )
    else:
        lines.append(
            f"Ritmo EN MARCHA (solo el tiempo evaluando senales; un hueco de mas de "
            f"{OFFLINE_GAP_HOURS:g} h sin ciclos cuenta como apagado): "
            f"APROBADA {_fmt_rate(status.rate_approved_running_per_day, '/dia en marcha')} -> "
            f"{_fmt_pace_days(status.days_needed_running('APROBADA'), 'dias EN MARCHA')}; "
            f"RECHAZADA {_fmt_rate(status.rate_rejected_running_per_day, '/dia en marcha')} -> "
            f"{_fmt_pace_days(status.days_needed_running('RECHAZADA'), 'dias EN MARCHA')}"
        )
        window_pct = (
            "n/d" if status.uptime_pct_window is None else f"{status.uptime_pct_window:.0%}"
        )
        total_pct = "n/d" if status.uptime_pct_total is None else f"{status.uptime_pct_total:.0%}"
        lines.append(
            f"Tiempo en marcha: {window_pct} de los ultimos {window} dias "
            f"({status.running_window_days:.1f} dias en marcha); {total_pct} desde el inicio de "
            "la medicion. Los dias EN MARCHA equivalen a dias de calendario solo si el bot no "
            "vuelve a apagarse."
        )
        if status.running_window_days < 1.0:
            lines.append(
                "(aviso: menos de 1 dia en marcha en la ventana -- la estimacion en marcha es "
                "volatil)"
            )
    return lines


def is_measurement_gate_open(status: MeasurementStatus) -> bool:
    """UNICA fuente de verdad: dice si la medicion ya junto `MIN_EFFECTIVE_N` por lado.

    Son conglomerados efectivos por lado. Abrir el gate NO basta para mostrar
    resultados: ver `is_outcome_reveal_allowed`.

    Args:
        status: el avance de la medicion.

    Returns:
        True si ambos lados alcanzaron el minimo (300).
    """
    return status.n_eff_approved >= MIN_EFFECTIVE_N and status.n_eff_rejected >= MIN_EFFECTIVE_N


def is_outcome_reveal_allowed(status: MeasurementStatus, docs_dir: Path | None = None) -> bool:
    """CONDICION UNICA para mostrar resultados (PnL, salida, motivo de cierre) de las sombras.

    Exige el gate abierto Y un registro oficial del veredicto en `docs/`
    (`app.trading.verdict_record`). Un registro sin gate abierto tampoco basta. Con el gate
    abierto y sin registro hay que seguir `docs/VEREDICTO_3_6.md`: copia, placebo oficial y
    veredicto; si el placebo sale NO INFORMATIVO se esperan 7 dias sin ver resultados
    (regla del 2026-10-17).

    Args:
        status: el avance de la medicion.
        docs_dir: carpeta donde buscar el registro; por defecto, `docs/` del repositorio.

    Returns:
        True si se pueden mostrar resultados.

    Efectos:
        Lee el directorio `docs/` (busca `VEREDICTO_3_6_REGISTRO*.txt`).
    """
    return is_measurement_gate_open(status) and (
        verdict_record.find_verdict_record(docs_dir) is not None
    )


def _build_status_from_rows(
    *, frozen_hash: str, rate_window_days: float, rows, known_keys, shadow_rows, labeled_rows,
    eval_marks: list[datetime] | None = None,
) -> MeasurementStatus:
    """Computo puro, comun a `compute_status`/`compute_status_async`: ya
    recibe las 5 consultas resueltas (filas de `llm_logs`, claves conocidas,
    sombras posteriores a la primera fila, las etiquetadas con su sombra
    unida y las marcas de evaluacion de los ciclos) -- separado para no
    duplicar esta logica entre la variante sincrona y la async."""
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

    # Mismo numerador (lo etiquetado en la ventana), pero dividido por el tiempo en que el
    # bot estuvo evaluando senales dentro de esa misma ventana.
    running_window_days = uptime_window = uptime_total = None
    rate_approved_running = rate_rejected_running = None
    if eval_marks:
        window_s = (data_now - window_start).total_seconds()
        total_s = (data_now - first_medicion_row_at).total_seconds()
        running_window_s = running_seconds(eval_marks, window_start, data_now)
        running_window_days = running_window_s / 86400.0
        uptime_window = running_window_s / window_s if window_s > 0 else None
        running_total_s = running_seconds(eval_marks, first_medicion_row_at, data_now)
        uptime_total = running_total_s / total_s if total_s > 0 else None
        if running_window_days > 0:
            rate_approved_running = n_eff_approved_window / running_window_days
            rate_rejected_running = n_eff_rejected_window / running_window_days

    return MeasurementStatus(
        frozen_hash=frozen_hash, total_medicion_rows=total_medicion_rows,
        ok_by_label=ok_by_label, approval_rate=approval_rate, within_band=within_band,
        non_ok_counts=non_ok_counts, json_repaired_count=json_repaired_count,
        total_cost_usd=total_cost_usd, omitted_without_row=omitted_without_row,
        first_medicion_row_at=first_medicion_row_at, n_eff_approved=n_eff_approved,
        n_eff_rejected=n_eff_rejected, window_days=window_days,
        rate_approved_per_day=rate_approved, rate_rejected_per_day=rate_rejected,
        data_now=data_now, running_window_days=running_window_days,
        uptime_pct_window=uptime_window, uptime_pct_total=uptime_total,
        rate_approved_running_per_day=rate_approved_running,
        rate_rejected_running_per_day=rate_rejected_running,
    )


def compute_status(
    conn: sqlite3.Connection,
    *,
    frozen_hash: str = FROZEN_PROMPT_SHA256,
    rate_window_days: float = RATE_WINDOW_DAYS,
) -> MeasurementStatus:
    """Avance de la medicion sobre un `sqlite3.Connection` de SOLO LECTURA (copia offline).

    Lo usa `scripts/measurement_status.py`. Nunca lee `pnl_*`, `exit_price` ni
    `close_reason`.

    Args:
        conn: conexion de solo lectura a una copia de la base.
        frozen_hash: hash del prompt de la medicion; por defecto, el congelado.
        rate_window_days: dias de la ventana del ritmo (se ancla al dato mas reciente de
            la copia, no al reloj).

    Returns:
        El `MeasurementStatus` (vacio si no hay filas de medicion).

    Efectos:
        Lee `llm_logs`, `shadow_trades` y `signals`.
    """
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
    eval_marks = parse_eval_marks(
        conn.execute(EVAL_MARKS_SQL, (eval_marks_lower_bound(first_medicion_row_at),)).fetchall()
    )

    return _build_status_from_rows(
        frozen_hash=frozen_hash, rate_window_days=rate_window_days, rows=rows,
        known_keys=known_keys, shadow_rows=shadow_rows, labeled_rows=labeled_rows,
        eval_marks=eval_marks,
    )


async def compute_status_async(
    db: Database,
    *,
    frozen_hash: str = FROZEN_PROMPT_SHA256,
    rate_window_days: float = RATE_WINDOW_DAYS,
) -> MeasurementStatus:
    """Lo mismo que `compute_status`, sobre la `Database` del bot (para la API y Telegram).

    Args:
        db: conexion a la base del bot.
        frozen_hash: hash del prompt de la medicion; por defecto, el congelado.
        rate_window_days: dias de la ventana del ritmo.

    Returns:
        El `MeasurementStatus`.

    Efectos:
        Lee `llm_logs`, `shadow_trades` y `signals`. Nunca PnL.
    """
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
    eval_marks = parse_eval_marks(
        await db.fetch_all(EVAL_MARKS_SQL, (eval_marks_lower_bound(first_medicion_row_at),))
    )

    return _build_status_from_rows(
        frozen_hash=frozen_hash, rate_window_days=rate_window_days, rows=rows,
        known_keys=known_keys, shadow_rows=shadow_rows, labeled_rows=labeled_rows,
        eval_marks=eval_marks,
    )
