"""Calibracion por placebo del criterio de valor de la IA (docs/FASE3_6_LLM.md
seccion f), sobre una COPIA de la base y en modo solo lectura.

Usa EXACTAMENTE los mismos filtros que el veredicto: `load_verdict_inputs`
(`app.trading.ai_value_report`) aplica `_verdict_inputs` -- grupos de la medicion
bajo el hash congelado, sin piloto, sin sombras no confiables (RECONCILE_FAILED o
reconciliacion fallida). El calculo en si es `ai_value.placebo_calibration`, sin
tocar.

**El placebo no revela nada de la IA.** Relabela al azar cada conglomerado con la
tasa de aprobacion observada, asi que la relacion entre las etiquetas REALES del LLM
y los resultados no interviene: solo la estructura de conglomerados, los retornos y
la tasa de aprobacion. Por eso puede correrse antes de que el gate se abra (y debe
registrarse antes de la primera decision real).

**Calibracion vacia.** Con pocos conglomerados casi ninguna repeticion llega a
`MIN_EFFECTIVE_N` por lado, todas dan INCONCLUSO y "Calibrado: SI" no dice nada.
`expected_valid_probability` es la probabilidad exacta (binomial: el N efectivo
aprobado de una repeticion es Binomial(conglomerados, tasa)) de que una repeticion
llegue al punto de analisis; el informe la imprime y advierte si es baja.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app.trading.ai_value import (
    APROBADA,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    DELTA_PCT,
    MIN_EFFECTIVE_N,
    PLACEBO_CALIBRATION_THRESHOLD,
    PLACEBO_REPEATS,
    PLACEBO_SEED,
    RECHAZADA,
    PlaceboResult,
    assign_clusters,
    placebo_calibration,
)
from app.trading.ai_value_report import check_frozen_hash_mode, load_verdict_inputs
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256

INFORMATIVE_VALID_FRACTION = 0.5


@dataclass(frozen=True)
class PlaceboRun:
    frozen_hash: str
    n_repeats: int
    groups_after_filters: int
    excluded_piloto: int
    excluded_unreliable: int
    n_closed_clusters: int
    n_labeled_closed: int
    n_approved: int
    approval_rate: float | None
    data_now: datetime | None
    expected_valid_probability: float | None
    result: PlaceboResult | None

    @property
    def informative(self) -> bool:
        return (
            self.expected_valid_probability is not None
            and self.expected_valid_probability >= INFORMATIVE_VALID_FRACTION
        )

    @property
    def conditional_rate(self) -> float | None:
        """Frecuencia de APORTA_VALOR si solo contaran las repeticiones que llegan
        al punto de analisis (estimacion: frecuencia / probabilidad de llegar)."""
        if self.result is None or not self.expected_valid_probability:
            return None
        return min(1.0, self.result.frequency / self.expected_valid_probability)


def prob_both_sides_reach(n_clusters: int, approval_rate: float, min_n: int) -> float:
    """P(min_n <= X <= n_clusters - min_n) con X ~ Binomial(n_clusters, approval_rate):
    la probabilidad de que una relabelacion al azar deje al menos `min_n`
    conglomerados en cada lado."""
    if n_clusters < 2 * min_n or not 0.0 < approval_rate < 1.0:
        return 0.0
    log_p, log_q = math.log(approval_rate), math.log(1.0 - approval_rate)
    log_fact_n = math.lgamma(n_clusters + 1)
    total = 0.0
    for x in range(min_n, n_clusters - min_n + 1):
        log_pmf = (
            log_fact_n - math.lgamma(x + 1) - math.lgamma(n_clusters - x + 1)
            + x * log_p + (n_clusters - x) * log_q
        )
        total += math.exp(log_pmf)
    return min(1.0, total)


def build_placebo(
    conn: sqlite3.Connection, *, frozen_hash: str = FROZEN_PROMPT_SHA256,
    allow_synthetic_for_tests: bool = False, n_repeats: int = PLACEBO_REPEATS,
) -> PlaceboRun:
    check_frozen_hash_mode(conn, frozen_hash, allow_synthetic_for_tests)
    inputs = load_verdict_inputs(conn, frozen_hash)
    filtered = inputs.filtered

    closed = [t for t in filtered if t.closed_at is not None and t.id is not None]
    labeled_closed = [t for t in closed if t.llm_decision in (APROBADA, RECHAZADA)]
    n_approved = sum(1 for t in labeled_closed if t.llm_decision == APROBADA)
    n_clusters = len(set(assign_clusters(closed).values()))
    measurement_times = [m.created_at for m in inputs.metas if m.key in inputs.measurement_keys]

    common = dict(
        frozen_hash=frozen_hash, n_repeats=n_repeats, groups_after_filters=len(filtered),
        excluded_piloto=len(inputs.piloto_keys),
        excluded_unreliable=len(inputs.measurement_keys & inputs.unreliable_keys),
        n_closed_clusters=n_clusters, n_labeled_closed=len(labeled_closed),
        n_approved=n_approved, data_now=max(measurement_times) if measurement_times else None,
    )
    if not labeled_closed:
        return PlaceboRun(
            approval_rate=None, expected_valid_probability=None, result=None, **common,
        )
    approval_rate = n_approved / len(labeled_closed)
    result = placebo_calibration(
        filtered, approval_rate, n_repeats=n_repeats, min_effective_n=MIN_EFFECTIVE_N,
        delta=DELTA_PCT,
    )
    valid_probability = prob_both_sides_reach(n_clusters, approval_rate, MIN_EFFECTIVE_N)
    return PlaceboRun(
        approval_rate=approval_rate, expected_valid_probability=valid_probability,
        result=result, **common,
    )


def render_placebo(run: PlaceboRun, *, run_at: datetime, elapsed_s: float | None = None) -> str:
    lines = [
        "=== Calibracion por placebo (docs/FASE3_6_LLM.md, seccion f) ===",
        "",
        f"Medicion bajo el hash congelado {run.frozen_hash}",
        f"Grupos tras los filtros del veredicto: {run.groups_after_filters} "
        f"(excluidos: {run.excluded_piloto} del piloto, {run.excluded_unreliable} de la "
        "medicion con reconciliacion fallida)",
        f"Conglomerados cerrados (universo del placebo): {run.n_closed_clusters}; operaciones "
        f"cerradas y etiquetadas: {run.n_labeled_closed}",
    ]
    if run.result is None or run.approval_rate is None:
        lines += [
            "",
            "No hay operaciones de la medicion, cerradas y con decision del LLM: nada que "
            "calibrar todavia.",
        ]
        return "\n".join(lines) + "\n"

    r = run.result
    valid = run.expected_valid_probability or 0.0
    lines += [
        f"Tasa de aprobacion observada: {run.approval_rate:.1%} ({run.n_approved} de "
        f"{run.n_labeled_closed})",
        f"Repeticiones: {r.n_repeats}; remuestreos del bootstrap: {BOOTSTRAP_RESAMPLES}; "
        f"MIN_EFFECTIVE_N={MIN_EFFECTIVE_N}, δ={DELTA_PCT:.0%}",
        "",
        f"APORTA_VALOR por azar: {r.aporta_valor_count} de {r.n_repeats} ({r.frequency:.1%})",
        f"IC 95 % de Wilson de esa frecuencia: [{r.ci_low:.1%}, {r.ci_high:.1%}]",
        f"Repeticiones que llegan al punto de analisis (>= {MIN_EFFECTIVE_N} conglomerados por "
        f"lado), esperadas: {valid:.1%} (~{valid * r.n_repeats:.0f} de {r.n_repeats}; "
        "binomial exacto)",
    ]
    if run.conditional_rate is not None:
        lines.append(
            f"Tasa estimada contando solo las repeticiones que llegan al punto de analisis: "
            f"{run.conditional_rate:.1%}"
        )
    lines.append(
        f"Calibrado (frecuencia < {PLACEBO_CALIBRATION_THRESHOLD:.0%}): "
        f"{'SI' if r.calibrated else 'NO'}"
    )
    if not r.calibrated:
        lines.append(
            "El criterio NO esta bien calibrado: no se usa para decidir hasta corregirlo "
            "(docs/FASE3_6_LLM.md, seccion f)."
        )
    if not run.informative:
        lines.append(
            f"ADVERTENCIA: solo ~{valid:.0%} de las repeticiones llegan al punto de analisis "
            f"(se piden >= {INFORMATIVE_VALID_FRACTION:.0%}); las demas dan INCONCLUSO por N "
            "insuficiente y no prueban nada. Este 'Calibrado' es poco informativo: repetirlo "
            "cuando haya mas conglomerados."
        )

    fecha = run_at.strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        "",
        "----- Para copiar al registro de docs/FASE3_6_LLM.md (seccion f) -----",
        f"**Placebo de la medicion, {fecha}**",
        f"- Hash del prompt: `{run.frozen_hash}`; datos hasta "
        f"{run.data_now.isoformat() if run.data_now else 'n/d'}.",
        f"- Conglomerados cerrados {run.n_closed_clusters}, operaciones etiquetadas "
        f"{run.n_labeled_closed}, tasa de aprobacion {run.approval_rate:.1%}.",
        f"- {r.n_repeats} repeticiones (semilla del placebo {PLACEBO_SEED}, del bootstrap "
        f"{BOOTSTRAP_SEED}, {BOOTSTRAP_RESAMPLES} remuestreos), N minimo {MIN_EFFECTIVE_N}, "
        f"δ {DELTA_PCT:.0%}.",
        f"- APORTA_VALOR por azar: {r.aporta_valor_count}/{r.n_repeats} ({r.frequency:.1%}), "
        f"IC 95 % de Wilson [{r.ci_low:.1%}, {r.ci_high:.1%}]; llegan al punto de analisis "
        f"~{valid:.0%} de las repeticiones.",
        f"- **Calibrado: {'SI' if r.calibrated else 'NO'}**"
        f"{'' if run.informative else ' (poco informativo, ver advertencia)'}.",
    ]
    if elapsed_s is not None:
        lines.append(f"- Duracion de la corrida: {elapsed_s / 60:.1f} min.")
    lines.append("----- fin del registro -----")
    return "\n".join(lines) + "\n"
