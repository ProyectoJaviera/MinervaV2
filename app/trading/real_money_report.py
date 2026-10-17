"""Reporte de paso a dinero real (subfase 3.8, docs/FASE3_PLAN.md punto 8).

**Solo RECOMIENDA. v1 es 100 % paper**: este modulo no importa ningun backend de
ejecucion ni cliente de red, nunca habilita nada, y su salida termina siempre
diciendo que la decision es manual y requeriria una fase nueva.

**Orden estricto (estructural, con test de que no se lee nada antes):**
1. `compute_status` + `is_outcome_reveal_allowed` (gate abierto Y registro oficial
   del veredicto). Si es falso: solo "que falta"; no se consulta `trades` ni ningun
   resultado.
2. Se lee el registro oficial (`verdict_record`); si NO dice APORTA_VALOR la
   conclusion es "no pasar a dinero real" y no se evalua nada mas.
3. Solo entonces se evaluan los criterios del brazo ejecutado (las operaciones
   REALES simuladas abiertas por el LLM, `decision_source = LLM_APROBADA`), mas los
   riesgos operativos medidos y la transferencia a las estrategias elegibles.

Los regimenes <30 / 30-100 / >=100 por lado de la fila 3.8 original se RETIRARON:
la medicion 3.6 fija un unico punto de analisis (300 conglomerados efectivos por
lado) y mostrar cifras antes contradice la regla de una sola mirada; el reporte
usa solo el veredicto del registro oficial, que CITA (nunca recalcula).

Cada criterio devuelve PASA / NO PASA / SIN DATOS; la falta de datos nunca cuenta
como "pasa". Criterio de rentabilidad (decidido el 2026-10-17): PF > 1,3 Y limite
inferior del IC 95 % del PF > 1,0 (bootstrap por conglomerados)."""

from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.backtesting.metrics import concentration_pct, stressed_profit_factor
from app.config import Settings
from app.persistence.models import Trade
from app.persistence.repositories.trades_repo import row_to_trade
from app.trading.ai_value import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CI_LEVEL,
    assign_clusters,
)
from app.trading.ai_value_report import (
    BucketResult,
    check_frozen_hash_mode,
    compute_breakdown,
    load_verdict_inputs,
)
from app.trading.measurement_status import (
    MIN_EFFECTIVE_N,
    MeasurementStatus,
    compute_status,
    is_measurement_gate_open,
    is_outcome_reveal_allowed,
)
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256
from app.trading.verdict_record import VerdictRecordInfo, load_official_record

STATE_NOT_ALLOWED = "REVEAL_NO_PERMITIDO"
STATE_NO_GO = "NO_PASAR_VEREDICTO"
STATE_EVALUATED = "EVALUADO"

PASA = "PASA"
NO_PASA = "NO PASA"
SIN_DATOS = "SIN DATOS"

RECOMMEND_MANUAL = "EVALUACION_MANUAL"
RECOMMEND_NO = "NO_PASAR"

# Contrato con la subfase 3.9 (conectar el brazo ejecutado en paper): las operaciones
# del brazo se marcan asi, y 3.9 fija estas dos claves al activarlo.
EXECUTED_ARM_DECISION_SOURCE = "LLM_APROBADA"
KEY_REAL_ARM_STARTED_AT = "real_arm_started_at"
KEY_REAL_ARM_DRAWDOWN_BASELINE = "real_arm_drawdown_baseline"
KEY_DRAWDOWN_TRIGGER_COUNT = "drawdown_stop_would_have_triggered_count"

PF_CI_FLOOR = 1.0
MAX_DRAWDOWN_TRIGGERS = 1
GAP_HOURS_THRESHOLD = 2.0
RECONCILE_LOG_MARKER = "Reconciliacion fallida"


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    status: str
    detail: str


@dataclass(frozen=True)
class SymbolExecutability:
    symbol: str
    min_trade_volume: float | None
    base_precision: int | None
    price: float | None
    qty: float | None
    status: str  # EJECUTABLE / NO EJECUTABLE / SIN PRECIO / SIN SPEC


@dataclass(frozen=True)
class DelayStats:
    n: int
    p50: float | None
    p95: float | None
    max: float | None
    over_limit: int
    limit_s: float


@dataclass(frozen=True)
class AvailabilityStats:
    hours_with_evaluations: int
    gaps_over_threshold: int
    longest_gap_hours: float | None


@dataclass(frozen=True)
class StressedPf:
    label: str
    pf: float | None


@dataclass
class OperationalMetrics:
    delays: DelayStats | None = None
    executability: list[SymbolExecutability] = field(default_factory=list)
    groups_in_non_executable_symbols: int = 0
    measured_groups: int = 0
    availability: AvailabilityStats | None = None
    stressed: list[StressedPf] = field(default_factory=list)
    fees_total_usdt: float | None = None
    funding_total_usdt: float | None = None


@dataclass
class RealMoneyReport:
    state: str
    frozen_hash: str
    status: MeasurementStatus
    missing: list[str] = field(default_factory=list)
    record: VerdictRecordInfo | None = None
    criteria: list[Criterion] = field(default_factory=list)
    recommendation: str | None = None
    operational: OperationalMetrics | None = None
    transfer: list[BucketResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- carga (solo lectura) ----------------------------------------------------------------


def _state_value(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_dt(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


def closed_with_pnl(trades: list[Trade]) -> list[Trade]:
    """Cerradas con PnL: una cerrada sin PnL (p.ej. RECONCILE_FAILED) no entra a
    ninguna metrica de rentabilidad -- la marca el criterio de reconciliacion."""
    return [t for t in trades if t.closed_at is not None and t.pnl_net_usdt is not None]


def _load_executed_trades(conn: sqlite3.Connection) -> list[Trade]:
    """Operaciones del brazo ejecutado (abiertas y cerradas). Hoy la tabla `trades`
    esta vacia: el brazo lo conecta la subfase 3.9."""
    rows = conn.execute(
        "SELECT * FROM trades WHERE decision_source = ? ORDER BY opened_at",
        (EXECUTED_ARM_DECISION_SOURCE,),
    ).fetchall()
    return [row_to_trade(r) for r in rows]


def _load_arm_state(conn: sqlite3.Connection) -> dict[str, str | None]:
    return {
        key: _state_value(conn, key)
        for key in (KEY_REAL_ARM_STARTED_AT, KEY_REAL_ARM_DRAWDOWN_BASELINE,
                    KEY_DRAWDOWN_TRIGGER_COUNT)
    }


def _load_decision_delays(conn: sqlite3.Connection, frozen_hash: str) -> list[float]:
    rows = conn.execute(
        "SELECT decision_delay_s FROM llm_logs WHERE fase = 'MEDICION' AND prompt_sha256 = ?",
        (frozen_hash,),
    ).fetchall()
    return [float(r["decision_delay_s"]) for r in rows]


def _load_signal_hours(conn: sqlite3.Connection, since: datetime) -> list[datetime]:
    rows = conn.execute(
        "SELECT DISTINCT substr(evaluated_at, 1, 13) AS h FROM signals WHERE evaluated_at >= ? "
        "ORDER BY h",
        (since.isoformat(),),
    ).fetchall()
    return [datetime.strptime(r["h"], "%Y-%m-%dT%H").replace(tzinfo=UTC) for r in rows]


def _load_universe_specs(conn: sqlite3.Connection) -> list[dict]:
    latest = conn.execute("SELECT MAX(refreshed_at) AS m FROM asset_universe").fetchone()["m"]
    if latest is None:
        return []
    symbols = [
        r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM asset_universe WHERE included = 1 AND refreshed_at = ? "
            "ORDER BY symbol", (latest,),
        )
    ]
    out = []
    for symbol in symbols:
        spec = conn.execute(
            "SELECT min_trade_volume, base_precision FROM contract_specs_cache WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        price = conn.execute(
            "SELECT close FROM ohlcv_cache WHERE symbol = ? AND interval = '1m' "
            "AND price_type = 'LAST_PRICE' ORDER BY open_time DESC LIMIT 1", (symbol,),
        ).fetchone()
        out.append({
            "symbol": symbol,
            "min_trade_volume": spec["min_trade_volume"] if spec else None,
            "base_precision": spec["base_precision"] if spec else None,
            "has_spec": spec is not None,
            "price": price["close"] if price else None,
        })
    return out


# --- calculo puro -----------------------------------------------------------------------------


def profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p <= 0)
    if gross_loss > 0:
        return gross_profit / gross_loss
    return math.inf if gross_profit > 0 else None


def pf_cluster_bootstrap(
    trades: list[Trade], *, n_resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None, float | None, int]:
    """(PF observado, limite inferior, limite superior del IC 95 %, conglomerados).
    Remuestrea CONGLOMERADOS (operaciones solapadas del mismo simbolo y direccion
    se mueven juntas), no operaciones sueltas."""
    closed = [t for t in closed_with_pnl(trades) if t.id is not None]
    clusters_of = assign_clusters(closed)  # type: ignore[arg-type]  # duck typing: mismos campos
    grouped: dict[int, list[float]] = {}
    for t in closed:
        grouped.setdefault(clusters_of[t.id], []).append(t.pnl_net_usdt)
    units = list(grouped.values())
    observed = profit_factor([p for unit in units for p in unit])
    if not units:
        return None, None, None, 0
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(n_resamples):
        sample = [units[rng.randrange(len(units))] for _ in units]
        pf = profit_factor([p for unit in sample for p in unit])
        if pf is not None:
            values.append(pf)
    if not values:
        return observed, None, None, len(units)
    values.sort()
    alpha = (1 - CI_LEVEL) / 2
    lo = values[int(alpha * (len(values) - 1))]
    hi = values[int((1 - alpha) * (len(values) - 1))]
    return observed, lo, hi, len(units)


def _fmt_pf(value: float | None) -> str:
    if value is None:
        return "n/d"
    return "inf" if math.isinf(value) else f"{value:.2f}"


def count_reconcile_log_lines(path: Path, since: datetime | None) -> int:
    """Lineas "Reconciliacion fallida" del log (CRITICAL). Best-effort: el log esta
    en hora LOCAL, asi que con `since` se filtra por fecha con un dia de margen."""
    floor = (since - timedelta(days=1)).strftime("%Y-%m-%d") if since is not None else None
    count = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if RECONCILE_LOG_MARKER not in line:
                continue
            if floor is not None and line[:10] < floor:
                continue
            count += 1
    return count


def evaluate_criteria(
    *, record: VerdictRecordInfo, trades: list[Trade], arm_state: dict[str, str | None],
    settings: Settings, log_reconcile_count: int | None = None,
) -> list[Criterion]:
    closed = closed_with_pnl(trades)
    out: list[Criterion] = [Criterion(
        "ia", "Valor de la IA (veredicto oficial 3.6)", PASA,
        f"{record.verdict}" + (" (MAGNITUD_BAJA: efecto real pero pequeno)"
                               if record.magnitud_baja else "")
        + f" segun {record.path.name}",
    )]

    # --- dias de paper trading ---
    started = arm_state.get(KEY_REAL_ARM_STARTED_AT)
    if not trades:
        out.append(Criterion("dias", f">= {settings.min_paper_trading_days} dias de paper trading",
                             SIN_DATOS, "no hay operaciones del brazo ejecutado (lo conecta 3.9)"))
    else:
        start = _parse_dt(started) if started else min(_aware(t.opened_at) for t in trades)
        end = max(_aware(t.closed_at or t.opened_at) for t in trades)
        days = (end - start).total_seconds() / 86400
        status = PASA if days >= settings.min_paper_trading_days else NO_PASA
        origin = (
            "real_arm_started_at" if started
            else "la primera operacion (falta real_arm_started_at)"
        )
        out.append(Criterion(
            "dias", f">= {settings.min_paper_trading_days} dias de paper trading", status,
            f"{days:.1f} dias desde {origin}",
        ))

    # --- operaciones cerradas + conglomerados ---
    pf, lo, hi, n_clusters = pf_cluster_bootstrap(closed)
    n = len(closed)
    if n == 0:
        out.append(Criterion("n", f">= {settings.min_closed_trades} operaciones cerradas",
                             SIN_DATOS, "0 operaciones cerradas"))
    else:
        out.append(Criterion(
            "n", f">= {settings.min_closed_trades} operaciones cerradas",
            PASA if n >= settings.min_closed_trades else NO_PASA,
            f"{n} cerradas, {n_clusters} conglomerados efectivos (operaciones solapadas del "
            "mismo simbolo y direccion cuentan como una)",
        ))

    # --- rentabilidad: PF > umbral Y limite inferior del IC > 1,0 ---
    label = f"PF > {settings.min_profit_factor} y limite inferior del IC 95 % > {PF_CI_FLOOR}"
    if n < settings.min_closed_trades:
        out.append(Criterion("pf", label, SIN_DATOS,
                             f"muestra insuficiente ({n} < {settings.min_closed_trades})"))
    elif pf is None or lo is None:
        out.append(Criterion("pf", label, SIN_DATOS, "PF indefinido"))
    elif pf == math.inf:
        out.append(Criterion("pf", label, SIN_DATOS,
                             "sin operaciones perdedoras: PF indefinido, no concluyente"))
    else:
        ok = pf > settings.min_profit_factor and lo > PF_CI_FLOOR
        out.append(Criterion(
            "pf", label, PASA if ok else NO_PASA,
            f"PF {_fmt_pf(pf)}, IC 95 % [{_fmt_pf(lo)}, {_fmt_pf(hi)}]",
        ))

    # --- drawdown ---
    baseline = arm_state.get(KEY_REAL_ARM_DRAWDOWN_BASELINE)
    counter = arm_state.get(KEY_DRAWDOWN_TRIGGER_COUNT)
    if baseline is None:
        out.append(Criterion(
            "drawdown", f"stop por drawdown activado <= {MAX_DRAWDOWN_TRIGGERS} vez en la ventana",
            SIN_DATOS, f"falta la linea base {KEY_REAL_ARM_DRAWDOWN_BASELINE} (la fija 3.9)",
        ))
    else:
        activations = int(counter or 0) - int(baseline)
        out.append(Criterion(
            "drawdown", f"stop por drawdown activado <= {MAX_DRAWDOWN_TRIGGERS} vez en la ventana",
            PASA if activations <= MAX_DRAWDOWN_TRIGGERS else NO_PASA,
            f"{activations} activaciones desde la linea base",
        ))

    # --- concentracion (la configuracion lo guarda como fraccion 0-1; el helper devuelve %) ---
    limit_pct = settings.backtest_concentration_limit_pct * 100
    label = f"concentracion <= {limit_pct:.0f} %"
    if not closed:
        out.append(Criterion("concentracion", label, SIN_DATOS, "0 operaciones cerradas"))
    else:
        concentration = concentration_pct(closed)  # type: ignore[arg-type]
        if concentration is None:
            out.append(Criterion(
                "concentracion", label, NO_PASA,
                "PnL neto total <= 0: no hay ganancia que repartir",
            ))
        else:
            out.append(Criterion(
                "concentracion", label, PASA if concentration <= limit_pct else NO_PASA,
                f"la mayor operacion o simbolo aporta {concentration:.1f} % del PnL neto",
            ))

    # --- reconciliacion ---
    label = "sin errores criticos de reconciliacion"
    if not trades:
        out.append(Criterion("reconciliacion", label, SIN_DATOS, "no hay operaciones"))
    else:
        flagged = sum(
            1 for t in trades
            if t.reconciliation_failed or t.fill_source == "TICK_UNRECONCILED"
            or t.close_reason == "RECONCILE_FAILED"
        )
        total = flagged + (log_reconcile_count or 0)
        source = "base" if log_reconcile_count is None else "base + log"
        note = (
            "" if log_reconcile_count is not None
            else " (sin --log-file: solo sustitutos de la base)"
        )
        out.append(Criterion(
            "reconciliacion", label, PASA if total == 0 else NO_PASA,
            f"{flagged} operaciones marcadas en la base"
            + (f", {log_reconcile_count} lineas CRITICAL en el log" if log_reconcile_count
               is not None else "")
            + f" ({source}){note}",
        ))
    return out


def recommend(criteria: list[Criterion]) -> str:
    return RECOMMEND_MANUAL if all(c.status == PASA for c in criteria) else RECOMMEND_NO


def compute_delay_stats(delays: list[float], limit_s: float) -> DelayStats | None:
    if not delays:
        return None
    ordered = sorted(delays)

    def pct(p: float) -> float:
        return ordered[int(round(p * (len(ordered) - 1)))]

    return DelayStats(
        n=len(ordered), p50=pct(0.5), p95=pct(0.95), max=ordered[-1],
        over_limit=sum(1 for d in ordered if d > limit_s), limit_s=limit_s,
    )


def classify_executability(
    specs: list[dict], *, margin_usdt: float, leverage: int,
) -> list[SymbolExecutability]:
    out = []
    for item in specs:
        price, min_vol, precision = item["price"], item["min_trade_volume"], item["base_precision"]
        if not item["has_spec"] or min_vol is None:
            out.append(SymbolExecutability(item["symbol"], min_vol, precision, price, None,
                                           "SIN SPEC"))
            continue
        if not price:
            out.append(SymbolExecutability(item["symbol"], min_vol, precision, price, None,
                                           "SIN PRECIO"))
            continue
        qty = margin_usdt * leverage / price
        if precision is not None:
            step = 10 ** -precision
            qty = math.floor(qty / step + 1e-9) * step
        out.append(SymbolExecutability(
            item["symbol"], min_vol, precision, price, qty,
            "EJECUTABLE" if qty >= min_vol else "NO EJECUTABLE",
        ))
    return out


def compute_availability(hours: list[datetime]) -> AvailabilityStats | None:
    if not hours:
        return None
    gaps = [
        (b - a).total_seconds() / 3600 for a, b in zip(hours, hours[1:], strict=False)
    ]
    over = [g for g in gaps if g > GAP_HOURS_THRESHOLD]
    return AvailabilityStats(
        hours_with_evaluations=len(hours), gaps_over_threshold=len(over),
        longest_gap_hours=max(gaps) if gaps else 0.0,
    )


def compute_stressed(trades: list[Trade]) -> list[StressedPf]:
    closed = closed_with_pnl(trades)
    adapted = [
        SimpleNamespace(
            pnl_gross_usdt=t.pnl_gross_usdt or 0.0, fee_entry_usdt=t.fee_entry_usdt,
            fee_exit_usdt=t.fee_exit_usdt or 0.0, funding_paid_usdt=t.funding_paid_usdt,
            slippage_cost_usdt=t.slippage_entry_usdt + t.slippage_exit_usdt,
        )
        for t in closed
    ]
    scenarios = (
        ("base", 1.0, 1.0), ("comisiones x2", 2.0, 1.0), ("slippage x2", 1.0, 2.0),
        ("slippage x3", 1.0, 3.0), ("comisiones x2 y slippage x3", 2.0, 3.0),
    )
    return [
        StressedPf(label, stressed_profit_factor(adapted, fee_m, slip_m))  # type: ignore[arg-type]
        for label, fee_m, slip_m in scenarios
    ]


# --- orquestacion --------------------------------------------------------------------------------


def build_report(
    conn: sqlite3.Connection, *, settings: Settings, frozen_hash: str = FROZEN_PROMPT_SHA256,
    allow_synthetic_for_tests: bool = False, docs_dir: Path | None = None,
    log_file: Path | None = None,
) -> RealMoneyReport:
    check_frozen_hash_mode(conn, frozen_hash, allow_synthetic_for_tests)

    # 1) Guardia: nada que lea resultados corre antes de esta comprobacion.
    status = compute_status(conn, frozen_hash=frozen_hash)
    record = load_official_record(docs_dir)
    if not is_outcome_reveal_allowed(status, docs_dir) or record is None:
        missing = []
        if not is_measurement_gate_open(status):
            missing.append(
                f"gate de la medicion CERRADO: APROBADA {status.n_eff_approved}/"
                f"{MIN_EFFECTIVE_N}, RECHAZADA {status.n_eff_rejected}/{MIN_EFFECTIVE_N}"
            )
        if record is None:
            missing.append(
                "no existe el registro oficial del veredicto "
                "(docs/VEREDICTO_3_6_REGISTRO*.txt con un veredicto valido)"
            )
        return RealMoneyReport(
            state=STATE_NOT_ALLOWED, frozen_hash=frozen_hash, status=status, missing=missing,
        )

    # 2) El veredicto oficial manda: si no es APORTA_VALOR no se evalua nada mas.
    if record.verdict != "APORTA_VALOR":
        return RealMoneyReport(
            state=STATE_NO_GO, frozen_hash=frozen_hash, status=status, record=record,
            recommendation=RECOMMEND_NO,
        )

    # 3) Evaluacion del brazo ejecutado y riesgos operativos medidos.
    trades = _load_executed_trades(conn)
    arm_state = _load_arm_state(conn)
    started = arm_state.get(KEY_REAL_ARM_STARTED_AT)
    since = _parse_dt(started) if started else min(
        (_aware(t.opened_at) for t in trades), default=None
    )
    log_count = count_reconcile_log_lines(log_file, since) if log_file is not None else None
    criteria = evaluate_criteria(
        record=record, trades=trades, arm_state=arm_state, settings=settings,
        log_reconcile_count=log_count,
    )

    inputs = load_verdict_inputs(conn, frozen_hash)
    operational = OperationalMetrics()
    operational.delays = compute_delay_stats(
        _load_decision_delays(conn, frozen_hash), settings.llm_real_max_delay_seconds
    )
    operational.executability = classify_executability(
        _load_universe_specs(conn), margin_usdt=settings.default_margin_usdt,
        leverage=settings.leverage,
    )
    non_executable = {s.symbol for s in operational.executability if s.status == "NO EJECUTABLE"}
    measured_light = [s for s in inputs.light if s.key in inputs.measurement_keys]
    operational.measured_groups = len(measured_light)
    operational.groups_in_non_executable_symbols = sum(
        1 for s in measured_light if s.symbol in non_executable
    )
    if status.first_medicion_row_at is not None:
        operational.availability = compute_availability(
            _load_signal_hours(conn, status.first_medicion_row_at)
        )
    closed = closed_with_pnl(trades)
    if closed:
        operational.stressed = compute_stressed(closed)
        operational.fees_total_usdt = sum(t.fee_entry_usdt + (t.fee_exit_usdt or 0.0)
                                          for t in closed)
        operational.funding_total_usdt = sum(t.funding_paid_usdt for t in closed)

    eligible = set(settings.real_account_eligible_strategies_list)
    transfer = [
        b for b in compute_breakdown(inputs.filtered, lambda t: t.strategy or "n/d")
        if not eligible or b.label in eligible
    ]
    notes = []
    if not trades:
        notes.append(
            "El brazo ejecutado (cuenta real simulada operando solo APROBADAS) todavia no "
            "existe: lo conecta la subfase 3.9. Hasta entonces los criterios de rentabilidad "
            "quedan SIN DATOS."
        )
    return RealMoneyReport(
        state=STATE_EVALUATED, frozen_hash=frozen_hash, status=status, record=record,
        criteria=criteria, recommendation=recommend(criteria), operational=operational,
        transfer=transfer, notes=notes,
    )


# --- texto --------------------------------------------------------------------------------------

DISCLAIMER = (
    "Este reporte solo RECOMIENDA. v1 es 100 % paper trading: nunca habilita ejecucion real "
    "(BitunixBackend sigue desactivado). Pasar a dinero real es una decision manual tuya y "
    "requeriria una fase nueva."
)


def _render_operational(op: OperationalMetrics, settings: Settings) -> list[str]:
    lines = ["## Riesgos operativos medidos (los no medibles: docs/PASO_DINERO_REAL.md)", ""]
    d = op.delays
    if d is None:
        lines.append("- Retraso de decision: sin filas de medicion.")
    else:
        lines.append(
            f"- Retraso de decision (n={d.n}): p50 {d.p50:.1f} s, p95 {d.p95:.1f} s, "
            f"max {d.max:.1f} s; {d.over_limit} de {d.n} superan el limite de {d.limit_s:.0f} s "
            "(esas aprobadas NO se abririan en la cuenta real)"
        )
    bad = [s for s in op.executability if s.status != "EJECUTABLE"]
    lines.append(
        f"- Tamano minimo de orden con margen {settings.default_margin_usdt:g} x "
        f"{settings.leverage}x: {len(op.executability) - len(bad)} de {len(op.executability)} "
        "simbolos del universo son ejecutables"
        + ("" if not bad else "; " + "; ".join(
            f"{s.symbol} {s.status}" + (
                f" (qty {s.qty:g} < minTradeVolume {s.min_trade_volume:g})"
                if s.status == "NO EJECUTABLE" and s.qty is not None else ""
            ) for s in bad))
    )
    if op.measured_groups:
        lines.append(
            f"- Grupos medidos en simbolos NO ejecutables: {op.groups_in_non_executable_symbols} "
            f"de {op.measured_groups} (el veredicto los incluye; la cuenta real no podria abrirlos)"
        )
    a = op.availability
    if a is not None:
        lines.append(
            f"- Disponibilidad del PC durante la medicion: {a.hours_with_evaluations} horas con "
            f"evaluaciones; {a.gaps_over_threshold} huecos > {GAP_HOURS_THRESHOLD:g} h; hueco "
            f"mas largo {a.longest_gap_hours:.1f} h"
        )
    if op.stressed:
        lines.append("- PF bajo estres de costos (brazo ejecutado): " + "; ".join(
            f"{s.label} {_fmt_pf(s.pf)}" for s in op.stressed))
        lines.append(
            f"- Comisiones totales {op.fees_total_usdt:.4f} USDT; funding total "
            f"{op.funding_total_usdt:+.4f} USDT"
        )
    else:
        lines.append("- PF estresado, comisiones y funding: SIN DATOS (brazo ejecutado vacio).")
    lines.append(
        "- Precio de entrada ejecutado vs sombra: SIN DATOS (requiere 3.9: "
        "signal_group_key en las operaciones ejecutadas)."
    )
    return lines + [""]


def render_report(report: RealMoneyReport, settings: Settings | None = None) -> str:
    lines = ["=== Reporte de paso a dinero real (subfase 3.8) ===", ""]
    if report.state == STATE_NOT_ALLOWED:
        lines += [
            "NO SE MUESTRA NINGUN RESULTADO: faltan condiciones previas.",
            "",
            "Falta:",
            *[f"- {item}" for item in report.missing],
            "",
            "Seguir docs/VEREDICTO_3_6.md (copia, placebo oficial y, solo si sale CALIBRADO, "
            "el veredicto con --out). Este reporte exige el gate abierto Y el registro "
            "oficial del veredicto.",
            "",
            DISCLAIMER,
        ]
        return "\n".join(lines) + "\n"

    record = report.record
    assert record is not None
    lines += ["## Veredicto oficial de valor de la IA (citado del registro, sin recalcular)", "",
              f"Registro: {record.path.name}", *record.quoted_lines, ""]
    if report.state == STATE_NO_GO:
        lines += [
            f"CONCLUSION: NO PASAR A DINERO REAL. El veredicto oficial es {record.verdict}, "
            "no APORTA_VALOR: no se evalua nada mas.",
            "",
            DISCLAIMER,
        ]
        return "\n".join(lines) + "\n"

    assert settings is not None
    lines += ["## Criterios del brazo ejecutado", ""]
    for c in report.criteria:
        lines.append(f"- [{c.status}] {c.label}: {c.detail}")
    lines.append("")
    if report.notes:
        lines += [*[f"NOTA: {n}" for n in report.notes], ""]
    if report.operational is not None:
        lines += _render_operational(report.operational, settings)
    lines += ["## Transferencia a las estrategias elegibles (EXPLORATORIO, no es criterio)", ""]
    if report.transfer:
        for b in report.transfer:
            if b.diff is None:
                lines.append(f"- {b.label}: N efectivo {b.n_eff_approved}/{b.n_eff_rejected}, "
                             "insuficiente para un IC")
            else:
                lines.append(
                    f"- {b.label}: N efectivo {b.n_eff_approved}/{b.n_eff_rejected}, "
                    f"diferencia {b.diff:.2%}, IC 95 % [{b.ci_low:.2%}, {b.ci_high:.2%}]"
                )
    else:
        lines.append("- Sin operaciones de las estrategias elegibles en la medicion.")
    lines.append("")
    if report.recommendation == RECOMMEND_MANUAL:
        lines.append(
            "RECOMENDACION: el reporte no detecta impedimentos en los criterios; pasar a "
            "EVALUACION MANUAL de dinero real (decision tuya, fuera de v1)."
        )
    else:
        pending = [c for c in report.criteria if c.status != PASA]
        lines.append(
            "RECOMENDACION: NO PASAR A DINERO REAL todavia. Pendiente: "
            + "; ".join(f"{c.label} [{c.status}]" for c in pending)
        )
    lines += ["", DISCLAIMER]
    return "\n".join(lines) + "\n"
