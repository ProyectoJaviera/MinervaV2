"""Criterio de valor de la IA sobre las operaciones sombra (subfase 3.6, fase ii).

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

**Fraccion del margen, no USDT.** `Δ` se mide sobre `pnl_net_usdt / margin_usdt`
de cada operacion, no sobre el PnL en USDT. Asi la comparacion vale igual con
`DEFAULT_MARGIN_USDT=5` que con el 10 que se usaba antes, y mezcla sin problema
operaciones de antes y despues de un cambio de margen (docs/FASE3_6_LLM.md,
seccion i).

**Tres veredictos, un solo punto de analisis (ver docs/FASE3_6_LLM.md, seccion i).**
El veredicto se calcula una sola vez, con `MIN_EFFECTIVE_N` conglomerados efectivos
por lado:

- `APORTA_VALOR`: el IC 95 % de `Δ` esta por encima de cero (`lo > 0`). Si adem{as
  queda por debajo de `DELTA_PCT` (`hi < δ`), se marca `magnitud_baja=True`: el
  efecto es real pero chico.
- `NO_APORTA_VALOR`: el IC 95 % esta por debajo de `δ` (`hi < δ`), sin que se haya
  dado la condicion anterior.
- `INCONCLUSO`: el resto -- N efectivo insuficiente, sin remuestreos validos, sin
  datos en ambos lados, el IC cruza entre cero y δ, o el SIN_LLM supera
  `MAX_SIN_LLM_SHARE` de los grupos (posible sesgo de exclusion -- esta condicion
  se mira ANTES que cualquier otra, sin ver el intervalo).

Solo se usan operaciones CERRADAS para `Δ`: un intervalo abierto no tiene final
conocido. El SIN_LLM se cuenta sobre TODOS los grupos (abiertos o cerrados), porque
mide la confiabilidad del propio proceso de decision, no el PnL.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.persistence.models import ShadowTrade

APROBADA = "APROBADA"
RECHAZADA = "RECHAZADA"
SIN_LLM = "SIN_LLM"

# Punto de analisis unico (docs/FASE3_6_LLM.md, seccion i): antes 100, ahora 300.
MIN_EFFECTIVE_N = 300
# delta: margen de no-valor, como fraccion del margen por operacion (3 %).
DELTA_PCT = 0.03
# Tope de SIN_LLM antes de INCONCLUSO por posible sesgo de exclusion (seccion m).
MAX_SIN_LLM_SHARE = 0.10

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261004
CI_LEVEL = 0.95
MIN_VALID_RESAMPLES = 50

PLACEBO_REPEATS = 1000
PLACEBO_SEED = 20261009
PLACEBO_CALIBRATION_THRESHOLD = 0.05


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
    approved_returns: list[float] = field(default_factory=list)
    rejected_returns: list[float] = field(default_factory=list)


def build_units(trades: list[ShadowTrade]) -> list[LabeledUnit]:
    """Una unidad por conglomerado, con el retorno (`pnl_net_usdt / margin_usdt`)
    de cada operacion cerrada y etiquetada (APROBADA/RECHAZADA), por lado."""
    assignment = assign_clusters(trades)
    units: dict[int, LabeledUnit] = {}
    for t in trades:
        if t.closed_at is None or t.id is None or t.llm_decision not in (APROBADA, RECHAZADA):
            continue
        unit = units.setdefault(assignment[t.id], LabeledUnit(cluster_id=assignment[t.id]))
        pnl = t.pnl_net_usdt or 0.0
        ret = pnl / t.margin_usdt if t.margin_usdt else 0.0
        (unit.approved_returns if t.llm_decision == APROBADA else unit.rejected_returns).append(ret)
    return list(units.values())


def cluster_bootstrap_difference(
    units: list[LabeledUnit],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float] | None:
    """(diferencia, ci_bajo, ci_alto) de la esperanza por operacion APROBADA menos
    RECHAZADA (como fraccion del margen), con remuestreo de CONGLOMERADOS al 95 %.
    None si no hay datos de ambos lados o casi ningun remuestreo es valido."""
    if not units:
        return None
    observed_app = [r for u in units for r in u.approved_returns]
    observed_rej = [r for u in units for r in u.rejected_returns]
    if not observed_app or not observed_rej:
        return None

    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n_resamples):
        sample = [units[rng.randrange(len(units))] for _ in units]
        app = [r for u in sample for r in u.approved_returns]
        rej = [r for u in sample for r in u.rejected_returns]
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
    total_groups: int
    sin_llm_count: int
    sin_llm_share: float | None
    verdict: str
    magnitud_baja: bool
    reason: str


def ai_value_verdict(
    trades: list[ShadowTrade],
    *,
    min_effective_n: int = MIN_EFFECTIVE_N,
    delta: float = DELTA_PCT,
    max_sin_llm_share: float = MAX_SIN_LLM_SHARE,
    piloto_keys: set[str] | None = None,
) -> AiValueVerdict:
    """Regla de tres veredictos fijada en `docs/FASE3_6_LLM.md`, seccion (i).
    `trades` son TODAS las operaciones sombra del periodo de medicion (abiertas y
    cerradas); si se pasan `piloto_keys` (de `llm_logs.fase = 'PILOTO'`), esos
    grupos se excluyen antes de cualquier calculo."""
    if piloto_keys:
        trades = [t for t in trades if t.signal_group_key not in piloto_keys]

    total_groups = len(trades)
    sin_llm_count = sum(1 for t in trades if t.llm_decision == SIN_LLM)
    sin_llm_share = (sin_llm_count / total_groups) if total_groups else None

    labeled = [
        t for t in trades
        if t.closed_at is not None and t.llm_decision in (APROBADA, RECHAZADA)
    ]
    n_raw_app = sum(1 for t in labeled if t.llm_decision == APROBADA)
    n_raw_rej = sum(1 for t in labeled if t.llm_decision == RECHAZADA)

    def _verdict(
        n_eff_app: int, n_eff_rej: int, diff: float | None, lo: float | None,
        hi: float | None, verdict: str, magnitud_baja: bool, reason: str,
    ) -> AiValueVerdict:
        return AiValueVerdict(
            n_raw_app, n_raw_rej, n_eff_app, n_eff_rej, diff, lo, hi,
            total_groups, sin_llm_count, sin_llm_share, verdict, magnitud_baja, reason,
        )

    if sin_llm_share is not None and sin_llm_share > max_sin_llm_share:
        return _verdict(
            0, 0, None, None, None, "INCONCLUSO", False,
            f"SIN_LLM es el {sin_llm_share:.1%} de los grupos (tope {max_sin_llm_share:.0%}): "
            "posible sesgo de exclusion; no se mira el intervalo",
        )

    if n_raw_app == 0 or n_raw_rej == 0:
        return _verdict(
            0, 0, None, None, None, "INCONCLUSO", False,
            "no hay operaciones cerradas con decision del LLM en ambos lados",
        )

    units = build_units(trades)
    n_eff_app = sum(1 for u in units if u.approved_returns)
    n_eff_rej = sum(1 for u in units if u.rejected_returns)
    boot = cluster_bootstrap_difference(units)
    diff, lo, hi = boot if boot is not None else (None, None, None)

    if n_eff_app < min_effective_n or n_eff_rej < min_effective_n:
        return _verdict(
            n_eff_app, n_eff_rej, diff, lo, hi, "INCONCLUSO", False,
            f"N efectivo insuficiente ({n_eff_app} aprobadas, {n_eff_rej} rechazadas; "
            f"se exigen {min_effective_n} por lado, punto de analisis unico)",
        )
    if boot is None:
        return _verdict(
            n_eff_app, n_eff_rej, None, None, None, "INCONCLUSO", False,
            "no hubo remuestreos validos del bootstrap",
        )

    if lo > 0:
        magnitud_baja = hi < delta
        reason = (
            f"intervalo del 95 % por encima de cero, pero por debajo de δ "
            f"({delta:.0%} del margen): efecto real y pequeño"
            if magnitud_baja else
            "intervalo del 95 % por encima de cero, con N efectivo suficiente"
        )
        return _verdict(n_eff_app, n_eff_rej, diff, lo, hi, "APORTA_VALOR", magnitud_baja, reason)

    if hi < delta:
        reason = (
            "el intervalo esta por debajo de cero: la IA resta valor"
            if hi < 0 else
            f"el intervalo esta por debajo de δ ({delta:.0%} del margen): no compensa "
            "el coste ni la dependencia de la API"
        )
        return _verdict(n_eff_app, n_eff_rej, diff, lo, hi, "NO_APORTA_VALOR", False, reason)

    return _verdict(
        n_eff_app, n_eff_rej, diff, lo, hi, "INCONCLUSO", False,
        f"el intervalo [{lo:.4f}, {hi:.4f}] cruza entre cero y δ ({delta:.0%}): "
        "la muestra no separa efecto real de ausencia de efecto",
    )


def _wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """IC 95 % de Wilson de una proporcion observada (seccion f: IC de la
    frecuencia del placebo). Evita depender de scipy para un calculo chico."""
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass(frozen=True)
class PlaceboResult:
    n_repeats: int
    aporta_valor_count: int
    frequency: float
    ci_low: float
    ci_high: float
    calibrated: bool


def placebo_calibration(
    trades: list[ShadowTrade],
    approval_rate: float,
    *,
    n_repeats: int = PLACEBO_REPEATS,
    seed: int = PLACEBO_SEED,
    min_effective_n: int = MIN_EFFECTIVE_N,
    delta: float = DELTA_PCT,
) -> PlaceboResult:
    """Calibracion por permutacion (docs/FASE3_6_LLM.md, seccion f): a cada
    CONGLOMERADO (no a cada operacion, para conservar la dependencia dentro del
    conglomerado) se le asigna una etiqueta al azar con probabilidad `approval_rate`
    (la tasa de aprobacion observada del LLM); se aplica la MISMA
    `ai_value_verdict` sobre esa relabelacion, y se repite `n_repeats` veces.

    Si el azar produce APORTA_VALOR con frecuencia >= 0,05, el criterio de
    decision no esta bien calibrado (`calibrated=False`) y no se usa para decidir
    hasta corregirlo. `ci_low`/`ci_high` son el IC 95 % de Wilson de esa
    frecuencia, informativo: con `n_repeats` finito el propio placebo tiene
    error de muestreo.

    Con el valor por defecto (1.000 repeticiones x 2.000 remuestreos del
    bootstrap = 2.000.000 remuestreos) puede tardar minutos con el volumen real
    de conglomerados; para un chequeo rapido o un test, bajar `n_repeats`."""
    closed = [t for t in trades if t.closed_at is not None and t.id is not None]
    clusters = assign_clusters(closed)
    cluster_ids = sorted(set(clusters.values()))
    rng = random.Random(seed)

    aporta_count = 0
    for _ in range(n_repeats):
        cluster_label = {
            c: (APROBADA if rng.random() < approval_rate else RECHAZADA) for c in cluster_ids
        }
        relabeled = [
            t.model_copy(update={"llm_decision": cluster_label[clusters[t.id]]}) for t in closed
        ]
        verdict = ai_value_verdict(
            relabeled, min_effective_n=min_effective_n, delta=delta, max_sin_llm_share=1.0,
        )
        if verdict.verdict == "APORTA_VALOR":
            aporta_count += 1

    frequency = aporta_count / n_repeats if n_repeats else 0.0
    ci_low, ci_high = _wilson_interval(aporta_count, n_repeats)
    return PlaceboResult(
        n_repeats=n_repeats, aporta_valor_count=aporta_count, frequency=frequency,
        ci_low=ci_low, ci_high=ci_high, calibrated=frequency < PLACEBO_CALIBRATION_THRESHOLD,
    )
