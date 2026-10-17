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

**Criterio de calibracion (decision del 2026-10-17, tomada antes de ver datos).**
"Calibrado" se decide con la tasa CONDICIONAL -- frecuencia de APORTA_VALOR por azar
dividida por la probabilidad de que una repeticion llegue al punto de analisis -- y
debe ser < 5 %; y solo si esa probabilidad es >= 50 %. Con una probabilidad menor,
casi todas las repeticiones dan INCONCLUSO por N insuficiente (calibracion vacia) y
el resultado es "NO INFORMATIVO": ni SI ni NO. La probabilidad es exacta (binomial: el
N efectivo aprobado de una repeticion es Binomial(conglomerados, tasa)). La frecuencia
bruta y su IC de Wilson se siguen imprimiendo, pero solo como informacion; el
`calibrated` de `ai_value.PlaceboResult` (frecuencia bruta) ya no decide.
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

CALIBRATED = "CALIBRADO"
NOT_CALIBRATED = "NO_CALIBRADO"
NOT_INFORMATIVE = "NO_INFORMATIVO"


def classify_calibration(frequency: float, valid_probability: float) -> tuple[str, float | None]:
    """(estado, tasa condicional). NO INFORMATIVO si la probabilidad de llegar al
    punto de analisis es < 50 % (no hay tasa condicional fiable); si no, CALIBRADO
    cuando frecuencia / probabilidad < 5 %."""
    if valid_probability < INFORMATIVE_VALID_FRACTION:
        return NOT_INFORMATIVE, None
    conditional = min(1.0, frequency / valid_probability)
    state = CALIBRATED if conditional < PLACEBO_CALIBRATION_THRESHOLD else NOT_CALIBRATED
    return state, conditional


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
    def calibration(self) -> str | None:
        """CALIBRADO / NO_CALIBRADO / NO_INFORMATIVO, o None si no hay nada que calibrar."""
        if self.result is None:
            return None
        return classify_calibration(
            self.result.frequency, self.expected_valid_probability or 0.0
        )[0]

    @property
    def conditional_rate(self) -> float | None:
        """Frecuencia de APORTA_VALOR si solo contaran las repeticiones que llegan
        al punto de analisis (frecuencia / probabilidad de llegar); None si no es
        informativo."""
        if self.result is None:
            return None
        return classify_calibration(
            self.result.frequency, self.expected_valid_probability or 0.0
        )[1]


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
    state = run.calibration
    cond = run.conditional_rate
    outcome = _outcome_text(state, cond, valid)
    lines += [
        f"Tasa de aprobacion observada: {run.approval_rate:.1%} ({run.n_approved} de "
        f"{run.n_labeled_closed})",
        f"Repeticiones: {r.n_repeats}; remuestreos del bootstrap: {BOOTSTRAP_RESAMPLES}; "
        f"MIN_EFFECTIVE_N={MIN_EFFECTIVE_N}, δ={DELTA_PCT:.0%}",
        "",
        f"Repeticiones que llegan al punto de analisis (>= {MIN_EFFECTIVE_N} conglomerados por "
        f"lado), esperadas: {valid:.1%} (~{valid * r.n_repeats:.0f} de {r.n_repeats}; "
        "binomial exacto)",
        f"Tasa condicional (frecuencia / probabilidad de llegar): "
        f"{'n/d (no informativo)' if cond is None else f'{cond:.1%}'}",
        f"[informativo] Frecuencia bruta de APORTA_VALOR por azar: {r.aporta_valor_count} de "
        f"{r.n_repeats} ({r.frequency:.1%})",
        f"[informativo] IC 95 % de Wilson de esa frecuencia bruta: "
        f"[{r.ci_low:.1%}, {r.ci_high:.1%}]",
        "",
        f"Regla (decision del 2026-10-17, antes de ver resultados): se decide con la tasa "
        f"condicional < {PLACEBO_CALIBRATION_THRESHOLD:.0%}, y solo si la probabilidad de "
        f"llegar al punto de analisis es >= {INFORMATIVE_VALID_FRACTION:.0%}.",
        f"Calibrado: {outcome}",
    ]
    if state == NOT_CALIBRATED:
        lines.append(
            "El criterio NO esta calibrado: no se usa para decidir; se revisa el diseno "
            "(docs/FASE3_6_LLM.md, seccion f)."
        )
    elif state == NOT_INFORMATIVE:
        lines.append(
            f"NO INFORMATIVO: solo ~{valid:.0%} de las repeticiones llegan al punto de analisis; "
            "las demas dan INCONCLUSO por N insuficiente y no prueban nada. NO correr el "
            "veredicto: esperar 7 dias mas de datos y repetir el procedimiento desde la copia "
            "(docs/VEREDICTO_3_6.md)."
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
        f"- Llegan al punto de analisis ~{valid:.1%} de las repeticiones; tasa condicional "
        f"{'n/d' if cond is None else f'{cond:.1%}'}.",
        f"- [informativo] APORTA_VALOR por azar (bruta): {r.aporta_valor_count}/{r.n_repeats} "
        f"({r.frequency:.1%}), IC 95 % de Wilson [{r.ci_low:.1%}, {r.ci_high:.1%}].",
        f"- **Calibrado: {_short_outcome(state)}** (regla del 2026-10-17: tasa condicional "
        f"< {PLACEBO_CALIBRATION_THRESHOLD:.0%} con probabilidad de llegar "
        f">= {INFORMATIVE_VALID_FRACTION:.0%}).",
    ]
    if elapsed_s is not None:
        lines.append(f"- Duracion de la corrida: {elapsed_s / 60:.1f} min.")
    lines.append("----- fin del registro -----")
    return "\n".join(lines) + "\n"


def _short_outcome(state: str | None) -> str:
    return {CALIBRATED: "SI", NOT_CALIBRATED: "NO", NOT_INFORMATIVE: "NO INFORMATIVO"}[state]


def _outcome_text(state: str | None, conditional: float | None, valid: float) -> str:
    if state == CALIBRATED:
        return (
            f"SI (tasa condicional {conditional:.1%} < {PLACEBO_CALIBRATION_THRESHOLD:.0%}, "
            f"probabilidad de llegar {valid:.1%} >= {INFORMATIVE_VALID_FRACTION:.0%})"
        )
    if state == NOT_CALIBRATED:
        return (
            f"NO (tasa condicional {conditional:.1%} >= {PLACEBO_CALIBRATION_THRESHOLD:.0%}, "
            f"probabilidad de llegar {valid:.1%})"
        )
    return (
        f"NO INFORMATIVO (probabilidad de llegar {valid:.1%} < "
        f"{INFORMATIVE_VALID_FRACTION:.0%})"
    )
