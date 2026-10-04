"""Criterio de valor de la IA sobre las operaciones sombra (subfase 3.5, ajuste 1).

**Por que la sombra permite solapes y el backtest no.** El backtest ignora una
senal mientras la misma celda (estrategia, simbolo, timeframe) tiene una posicion
abierta (`docs/FASE2_CRITERIOS.md`, punto 6). La sombra NO bloquea: abre una
operacion por cada senal agrupada aunque otra del mismo simbolo siga abierta. Si
bloqueara, con el LLM una operacion RECHAZADA impediria abrir la siguiente, y las
APROBADA y RECHAZADA dejarian de compararse sobre las mismas oportunidades: la
comparacion quedaria sesgada por la propia decision del LLM. Permitir solapes
mantiene todas las senales, pero hace que las operaciones no sean independientes.

**Conglomerados.** Dos operaciones del mismo simbolo y direccion cuyos intervalos
de vida [apertura, cierre] se solapan cuentan como UNA unidad: se mueven juntas
con el mismo movimiento de mercado. El N efectivo es el numero de unidades. El
bootstrap remuestrea unidades (no operaciones), para no tratar como independientes
trades que comparten el mismo movimiento de precio.

Solo se usan operaciones CERRADAS: un intervalo abierto no tiene final conocido.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.persistence.models import ShadowTrade

APROBADA = "APROBADA"
RECHAZADA = "RECHAZADA"
MIN_EFFECTIVE_N = 100
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261004
CI_LEVEL = 0.95
MIN_VALID_RESAMPLES = 50


def assign_clusters(trades: list[ShadowTrade]) -> dict[int, int]:
    """trade.id -> indice de conglomerado. Solo operaciones cerradas. Dentro de cada
    (simbolo, direccion), se fusionan los intervalos que se solapan."""
    closed = [t for t in trades if t.closed_at is not None and t.id is not None]
    groups: dict[tuple[str, str], list[ShadowTrade]] = {}
    for t in closed:
        groups.setdefault((t.symbol, t.side.value), []).append(t)

    assignment: dict[int, int] = {}
    next_cluster = 0
    for members in groups.values():
        members.sort(key=lambda t: t.opened_at)
        current_end = None
        for t in members:
            if current_end is None or t.opened_at >= current_end:
                next_cluster += 1
                current_end = t.closed_at
            else:
                current_end = max(current_end, t.closed_at)
            assignment[t.id] = next_cluster
    return assignment


@dataclass
class LabeledUnit:
    cluster_id: int
    approved_pnls: list[float] = field(default_factory=list)
    rejected_pnls: list[float] = field(default_factory=list)


def build_units(trades: list[ShadowTrade]) -> list[LabeledUnit]:
    assignment = assign_clusters(trades)
    units: dict[int, LabeledUnit] = {}
    for t in trades:
        if t.closed_at is None or t.id is None or t.llm_decision not in (APROBADA, RECHAZADA):
            continue
        unit = units.setdefault(assignment[t.id], LabeledUnit(cluster_id=assignment[t.id]))
        pnl = t.pnl_net_usdt or 0.0
        (unit.approved_pnls if t.llm_decision == APROBADA else unit.rejected_pnls).append(pnl)
    return list(units.values())


def cluster_bootstrap_difference(
    units: list[LabeledUnit],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float] | None:
    """(diferencia, ci_bajo, ci_alto) de la esperanza por operacion APROBADA menos
    RECHAZADA, con remuestreo de CONGLOMERADOS al 95 %. None si no hay datos de
    ambos lados o casi ningun remuestreo es valido."""
    if not units:
        return None
    observed_app = [p for u in units for p in u.approved_pnls]
    observed_rej = [p for u in units for p in u.rejected_pnls]
    if not observed_app or not observed_rej:
        return None

    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n_resamples):
        sample = [units[rng.randrange(len(units))] for _ in units]
        app = [p for u in sample for p in u.approved_pnls]
        rej = [p for u in sample for p in u.rejected_pnls]
        if app and rej:
            diffs.append(sum(app) / len(app) - sum(rej) / len(rej))
    if len(diffs) < MIN_VALID_RESAMPLES:
        return None
    diffs.sort()
    alpha = (1 - CI_LEVEL) / 2
    lo = diffs[int(alpha * (len(diffs) - 1))]
    hi = diffs[int((1 - alpha) * (len(diffs) - 1))]
    diff = sum(observed_app) / len(observed_app) - sum(observed_rej) / len(observed_rej)
    return diff, lo, hi


@dataclass(frozen=True)
class AiValueVerdict:
    n_raw_approved: int
    n_raw_rejected: int
    n_eff_approved: int
    n_eff_rejected: int
    diff: float | None
    ci_low: float | None
    ci_high: float | None
    verdict: str
    reason: str


def ai_value_verdict(
    trades: list[ShadowTrade],
    min_effective_n: int = MIN_EFFECTIVE_N,
) -> AiValueVerdict:
    """Regla fijada (docs/FASE3_PLAN.md, seccion 8): sin intervalo de confianza del
    95 % que excluya el cero con N efectivo >= `min_effective_n` por lado, la
    conclusion es "la IA no aporta valor" y se detiene el gasto en la API."""
    labeled = [
        t for t in trades
        if t.closed_at is not None and t.llm_decision in (APROBADA, RECHAZADA)
    ]
    n_raw_app = sum(1 for t in labeled if t.llm_decision == APROBADA)
    n_raw_rej = sum(1 for t in labeled if t.llm_decision == RECHAZADA)
    if n_raw_app == 0 or n_raw_rej == 0:
        return AiValueVerdict(
            n_raw_app, n_raw_rej, 0, 0, None, None, None, "SIN_DATOS",
            "no hay operaciones cerradas con decision del LLM en ambos lados",
        )

    units = build_units(trades)
    n_eff_app = sum(1 for u in units if u.approved_pnls)
    n_eff_rej = sum(1 for u in units if u.rejected_pnls)
    boot = cluster_bootstrap_difference(units)
    diff, lo, hi = boot if boot is not None else (None, None, None)

    if n_eff_app < min_effective_n or n_eff_rej < min_effective_n:
        reason = (f"N efectivo insuficiente ({n_eff_app} aprobadas, {n_eff_rej} rechazadas; "
                  f"se exigen {min_effective_n} por lado)")
    elif boot is None:
        reason = "no hubo remuestreos validos del bootstrap"
    elif lo <= 0 <= hi:
        reason = "el intervalo de confianza del 95 % incluye el cero"
    elif hi < 0:
        reason = "el intervalo esta por debajo del cero: la IA resta valor"
    else:
        return AiValueVerdict(
            n_raw_app, n_raw_rej, n_eff_app, n_eff_rej, diff, lo, hi,
            "APORTA_VALOR", "intervalo del 95 % por encima del cero con N efectivo suficiente",
        )
    return AiValueVerdict(
        n_raw_app, n_raw_rej, n_eff_app, n_eff_rej, diff, lo, hi,
        "LA_IA_NO_APORTA_VALOR", reason,
    )
