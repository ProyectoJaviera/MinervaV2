"""Informe del veredicto de valor de la IA (subfase 3.6, punto de analisis unico
de 300 conglomerados efectivos por lado; docs/FASE3_6_LLM.md secciones (i) y (m)).

**Solo lectura, sobre una COPIA de la base.** Todo entra por un
`sqlite3.Connection` abierto en modo solo lectura (`scripts/ai_value_report.py`).

**Guardia del gate, estructural.** `build_report` empieza con `compute_status`
(consultas SIN PnL) y, si `is_measurement_gate_open` es falso, devuelve sin haber
ejecutado nunca la consulta que lee `pnl_*`/`exit_price`/`close_reason`
(`_load_outcome_trades`). Mirar resultados antes de `MIN_EFFECTIVE_N` por lado
contaminaria el veredicto (seccion i).

**Guardia estricta del punto de analisis.** El gate de `measurement_status` no
excluye las sombras `RECONCILE_FAILED` ni las del piloto, pero `ai_value_verdict`
si. Si el gate abre y, tras esas exclusiones, el N efectivo que usaria el veredicto
es < `MIN_EFFECTIVE_N` en algun lado, el informe declara "punto de analisis no
alcanzado" y NO muestra ni Δ ni IC ni desglose (`ai_value_verdict` los devuelve
aun con N insuficiente; esta funcion no se modifica, se suprime aqui).

**Datos sinteticos.** `allow_synthetic_for_tests` solo se acepta si la base es
positivamente sintetica (`assert_synthetic_database`): todas las filas de
`llm_logs` con `model == SYNTHETIC_MODEL` y ninguna con el hash congelado real.
Una copia de la base real siempre tiene el modelo real y el hash real, asi que la
bandera nunca puede destapar resultados reales.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from app.persistence.llm_log_fields import atr_value
from app.persistence.models import ShadowTrade
from app.persistence.repositories.shadow_repo import row_to_shadow
from app.trading.ai_value import (
    APROBADA,
    DELTA_PCT,
    MAX_SIN_LLM_SHARE,
    MIN_EFFECTIVE_N,
    RECHAZADA,
    AiValueVerdict,
    ai_value_verdict,
    build_units,
    cluster_bootstrap_difference,
)
from app.trading.measurement_status import (
    FASE_MEDICION,
    MeasurementStatus,
    compute_status,
    is_measurement_gate_open,
)
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256

SYNTHETIC_MODEL = "synthetic-test"
BREAKDOWN_MIN_EFFECTIVE_N = 30
HOUR_BIN_SIZE = 4

STATE_GATE_CLOSED = "GATE_CERRADO"
STATE_POINT_NOT_REACHED = "PUNTO_NO_ALCANZADO"
STATE_VERDICT = "VEREDICTO"


class SyntheticGuardError(Exception):
    """La base no es positivamente sintetica: la bandera de pruebas se rechaza."""


@dataclass(frozen=True)
class LogMeta:
    """Metadatos de una fila de `llm_logs`, sin respuesta ni prompt.

    Clave del grupo, fase, hash del prompt, estado, hora UTC, ATR y si el JSON se reparo.
    """
    key: str
    fase: str
    prompt_sha256: str
    status: str
    json_repaired: bool
    hour_utc: int
    atr_pct: float | None
    created_at: datetime


@dataclass(frozen=True)
class LightShadow:
    """Una sombra SIN ningun campo de resultado (ni PnL, ni precio de salida, ni
    motivo de cierre): lo necesario para clasificar grupos en la seccion (m)."""

    key: str
    symbol: str
    side: str
    strategy: str
    candle_close_time: datetime
    unreliable: bool


@dataclass(frozen=True)
class GroupPartition:
    """Particion de todos los grupos sombra segun hayan pasado o no por el LLM de la medicion."""
    total_groups: int
    measurement: int
    measurement_unreliable: int
    piloto: int
    other_hash: int
    omitted_pre_activation: int
    omitted_post_activation: int


@dataclass(frozen=True)
class OmittedProfile:
    """Perfil (sin PnL) de los omitidos posteriores a la activacion frente a los
    grupos medidos, para detectar si el salto de la medicion fue selectivo."""

    measured_by_side: dict[str, int]
    omitted_by_side: dict[str, int]
    measured_by_strategy: dict[str, int]
    omitted_by_strategy: dict[str, int]


@dataclass(frozen=True)
class StratumShare:
    """Peso de un estrato en todos los grupos medidos frente a su peso entre los SIN_LLM."""
    label: str
    all_share: float
    sin_llm_share: float | None
    ratio: float | None


@dataclass(frozen=True)
class SinLlmDiagnostics:
    """SIN_LLM por causa, franja horaria UTC y tercil de volatilidad (seccion m del informe)."""
    groups_with_log: int
    sin_llm_by_cause: dict[str, int]
    sin_llm_rows: int
    by_hour_bin: list[StratumShare]
    by_atr_tercile: list[StratumShare]


@dataclass(frozen=True)
class SensitivityResult:
    """Veredicto recalculado sin las filas con JSON reparado, y si coincide con el principal."""
    excluded_groups: int
    verdict: AiValueVerdict | None
    robust: bool | None


@dataclass(frozen=True)
class BucketResult:
    """Resultado de un cubo del desglose (estrategia o simbolo).

    `diff` es la diferencia APROBADA menos RECHAZADA; `None` si el N es insuficiente.
    """
    label: str
    n_trades: int
    n_eff_approved: int
    n_eff_rejected: int
    n_mixed: int
    diff: float | None
    ci_low: float | None
    ci_high: float | None


@dataclass(frozen=True)
class ReportResult:
    """Salida estructurada del informe del veredicto.

    `state` es `GATE_CERRADO`, `PUNTO_NO_ALCANZADO` o `VEREDICTO`; solo en el ultimo estan
    `verdict`, `sensitivity` y los desgloses.
    """
    state: str
    frozen_hash: str
    status: MeasurementStatus
    clean_n_approved: int = 0
    clean_n_rejected: int = 0
    partition: GroupPartition | None = None
    omitted_profile: OmittedProfile | None = None
    diagnostics: SinLlmDiagnostics | None = None
    json_repaired_groups: int = 0
    verdict: AiValueVerdict | None = None
    sensitivity: SensitivityResult | None = None
    by_strategy: list[BucketResult] = field(default_factory=list)
    by_symbol: list[BucketResult] = field(default_factory=list)


# --- guardias --------------------------------------------------------------------


def assert_synthetic_database(conn: sqlite3.Connection) -> None:
    """Comprueba que la base es POSITIVAMENTE sintetica; si no, rechaza la bandera de pruebas.

    Args:
        conn: conexion de solo lectura.

    Raises:
        SyntheticGuardError: si `llm_logs` esta vacia, tiene un modelo distinto de
            `synthetic-test` o tiene filas con el hash congelado real.

    Efectos:
        Lee `llm_logs`.
    """
    rows = conn.execute("SELECT DISTINCT model, prompt_sha256 FROM llm_logs").fetchall()
    if not rows:
        raise SyntheticGuardError(
            "la base no tiene filas en llm_logs: no se puede probar que es sintetica"
        )
    for row in rows:
        if row["model"] != SYNTHETIC_MODEL:
            raise SyntheticGuardError(
                f"hay filas de llm_logs con model={row['model']!r} (no {SYNTHETIC_MODEL!r}): "
                "la base no es sintetica, la bandera de pruebas no aplica"
            )
        if row["prompt_sha256"] == FROZEN_PROMPT_SHA256:
            raise SyntheticGuardError(
                "hay filas con el hash congelado real: la base no es sintetica, "
                "la bandera de pruebas no aplica"
            )


# --- carga (solo lectura) ---------------------------------------------------------


def _load_log_metas(conn: sqlite3.Connection) -> list[LogMeta]:
    # `atr_pct`: el etiquetador en vivo no rellena la columna, pero el mensaje que vio el modelo
    # (con `volatility_atr14_pct`) queda en `prompt`; solo se trae el prompt de las filas con la
    # columna nula (ver `app/persistence/llm_log_fields.py`). Solo lectura.
    rows = conn.execute(
        "SELECT signal_group_key, fase, prompt_sha256, status, json_repaired, hour_utc, "
        "atr_pct, CASE WHEN atr_pct IS NULL THEN prompt END AS prompt_for_atr, created_at "
        "FROM llm_logs"
    ).fetchall()
    return [
        LogMeta(
            key=r["signal_group_key"], fase=r["fase"], prompt_sha256=r["prompt_sha256"],
            status=r["status"], json_repaired=bool(r["json_repaired"]),
            hour_utc=r["hour_utc"], atr_pct=atr_value(r["atr_pct"], r["prompt_for_atr"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


def _load_light_shadows(conn: sqlite3.Connection) -> list[LightShadow]:
    # `close_reason` solo entra en la expresion booleana `unreliable` (misma
    # definicion que `shadow_repo.get_unreliable_signal_group_keys`); nunca se
    # selecciona como columna.
    rows = conn.execute(
        "SELECT signal_group_key, symbol, side, strategy, candle_close_time, "
        "(reconciliation_failed = 1 OR close_reason = 'RECONCILE_FAILED') AS unreliable "
        "FROM shadow_trades"
    ).fetchall()
    return [
        LightShadow(
            key=r["signal_group_key"], symbol=r["symbol"], side=r["side"],
            strategy=r["strategy"],
            candle_close_time=datetime.fromisoformat(r["candle_close_time"]),
            unreliable=bool(r["unreliable"]),
        )
        for r in rows
    ]


def _load_outcome_trades(conn: sqlite3.Connection) -> list[ShadowTrade]:
    """UNICO lugar que lee resultados (PnL, salida, motivo de cierre). Solo se
    llama con el gate abierto."""
    return [row_to_shadow(r) for r in conn.execute("SELECT * FROM shadow_trades").fetchall()]


# --- seccion (m) ----------------------------------------------------------------------


def compute_partition(
    shadows: list[LightShadow], metas: list[LogMeta], frozen_hash: str,
) -> GroupPartition:
    """Cuenta los grupos sombra por categoria (seccion m): medidos, piloto, otro hash y omitidos.

    Args:
        shadows: sombras sin campos de resultado.
        metas: metadatos de `llm_logs`.
        frozen_hash: hash del prompt de la medicion.

    Returns:
        La `GroupPartition`.
    """
    meta_by_key = {m.key: m for m in metas}
    frozen_times = [
        m.created_at for m in metas if m.fase == FASE_MEDICION and m.prompt_sha256 == frozen_hash
    ]
    first_frozen = min(frozen_times) if frozen_times else None

    measurement = unreliable = piloto = other_hash = pre = post = 0
    for s in shadows:
        meta = meta_by_key.get(s.key)
        if meta is None:
            if first_frozen is not None and s.candle_close_time > first_frozen:
                post += 1
            else:
                pre += 1
        elif meta.fase != FASE_MEDICION:
            piloto += 1
        elif meta.prompt_sha256 != frozen_hash:
            other_hash += 1
        else:
            measurement += 1
            if s.unreliable:
                unreliable += 1
    return GroupPartition(
        total_groups=len(shadows), measurement=measurement, measurement_unreliable=unreliable,
        piloto=piloto, other_hash=other_hash, omitted_pre_activation=pre,
        omitted_post_activation=post,
    )


def compute_omitted_profile(
    shadows: list[LightShadow], metas: list[LogMeta], frozen_hash: str,
) -> OmittedProfile:
    """Perfil (sin PnL) de los omitidos posteriores a la activacion frente a los medidos.

    Detecta si el salto de la medicion fue selectivo.

    Args:
        shadows: sombras sin campos de resultado.
        metas: metadatos de `llm_logs`.
        frozen_hash: hash del prompt de la medicion.

    Returns:
        El `OmittedProfile`.
    """
    meta_by_key = {m.key: m for m in metas}
    frozen_times = [
        m.created_at for m in metas if m.fase == FASE_MEDICION and m.prompt_sha256 == frozen_hash
    ]
    first_frozen = min(frozen_times) if frozen_times else None
    measured_side: dict[str, int] = {}
    omitted_side: dict[str, int] = {}
    measured_strat: dict[str, int] = {}
    omitted_strat: dict[str, int] = {}
    for s in shadows:
        meta = meta_by_key.get(s.key)
        if meta is None:
            if first_frozen is not None and s.candle_close_time > first_frozen:
                omitted_side[s.side] = omitted_side.get(s.side, 0) + 1
                omitted_strat[s.strategy] = omitted_strat.get(s.strategy, 0) + 1
        elif (
            meta.fase == FASE_MEDICION and meta.prompt_sha256 == frozen_hash and not s.unreliable
        ):
            measured_side[s.side] = measured_side.get(s.side, 0) + 1
            measured_strat[s.strategy] = measured_strat.get(s.strategy, 0) + 1
    return OmittedProfile(measured_side, omitted_side, measured_strat, omitted_strat)


def _strata_shares(
    labels: list[str], is_sin_llm: list[bool], order: list[str],
) -> list[StratumShare]:
    total = len(labels)
    sin_total = sum(is_sin_llm)
    out = []
    for label in order:
        in_stratum = [i for i, lab in enumerate(labels) if lab == label]
        all_share = len(in_stratum) / total if total else 0.0
        if sin_total:
            sin_share: float | None = sum(1 for i in in_stratum if is_sin_llm[i]) / sin_total
        else:
            sin_share = None
        ratio = sin_share / all_share if sin_share is not None and all_share > 0 else None
        out.append(StratumShare(label, all_share, sin_share, ratio))
    return out


def hour_bin_label(hour_utc: int) -> str:
    """La etiqueta de la franja de 4 horas UTC a la que pertenece una hora.

    Args:
        hour_utc: hora UTC (0-23).

    Returns:
        Texto como `04-07 UTC`.
    """
    start = (hour_utc // HOUR_BIN_SIZE) * HOUR_BIN_SIZE
    return f"{start:02d}-{start + HOUR_BIN_SIZE - 1:02d} UTC"


def compute_sin_llm_diagnostics(measured_metas: list[LogMeta]) -> SinLlmDiagnostics:
    """SIN_LLM por causa y su distribucion por franja horaria y por tercil de `atr_pct`.

    Un grupo SIN_LLM es una fila de `llm_logs` con `status != 'OK'`.

    Args:
        measured_metas: metadatos de los grupos medidos.

    Returns:
        Los `SinLlmDiagnostics`.
    """
    causes: dict[str, int] = {}
    for m in measured_metas:
        if m.status != "OK":
            causes[m.status] = causes.get(m.status, 0) + 1
    is_sin = [m.status != "OK" for m in measured_metas]

    hour_labels = [hour_bin_label(m.hour_utc) for m in measured_metas]
    hour_order = [hour_bin_label(h) for h in range(0, 24, HOUR_BIN_SIZE)]
    by_hour = _strata_shares(hour_labels, is_sin, hour_order)

    atr_values = sorted(m.atr_pct for m in measured_metas if m.atr_pct is not None)
    if len(atr_values) >= 3:
        c1 = atr_values[len(atr_values) // 3]
        c2 = atr_values[(2 * len(atr_values)) // 3]

        def _tercile(atr: float | None) -> str:
            if atr is None:
                return "n/d"
            return "bajo" if atr < c1 else ("medio" if atr < c2 else "alto")

        atr_labels = [_tercile(m.atr_pct) for m in measured_metas]
        by_atr = _strata_shares(atr_labels, is_sin, ["bajo", "medio", "alto", "n/d"])
        by_atr = [s for s in by_atr if s.label != "n/d" or s.all_share > 0]
    else:
        by_atr = []

    return SinLlmDiagnostics(
        groups_with_log=len(measured_metas), sin_llm_by_cause=causes, sin_llm_rows=sum(is_sin),
        by_hour_bin=by_hour, by_atr_tercile=by_atr,
    )


# --- veredicto, sensibilidad y desglose ---------------------------------------------


def _verdict_inputs(
    trades: list[ShadowTrade], measurement_keys: set[str], piloto_keys: set[str],
    unreliable_keys: set[str],
) -> list[ShadowTrade]:
    """Mismo filtrado que aplica `ai_value_verdict` a sus tres conjuntos de claves."""
    kept = [t for t in trades if t.signal_group_key in measurement_keys]
    kept = [t for t in kept if t.signal_group_key not in piloto_keys]
    return [t for t in kept if t.signal_group_key not in unreliable_keys]


def clean_effective_n(trades: list[ShadowTrade]) -> tuple[int, int]:
    """N efectivo (conglomerados) aprobado y rechazado de las sombras ya filtradas.

    Args:
        trades: sombras tras los tres filtros.

    Returns:
        La tupla `(aprobadas, rechazadas)`.
    """
    units = build_units(trades)
    return (
        sum(1 for u in units if u.approved_returns),
        sum(1 for u in units if u.rejected_returns),
    )


def compute_sensitivity(
    trades: list[ShadowTrade], main: AiValueVerdict, *, measurement_keys: set[str],
    piloto_keys: set[str], unreliable_keys: set[str], repaired_keys: set[str],
) -> SensitivityResult:
    """Veredicto recalculado sin los grupos cuya fila de `llm_logs` tiene `json_repaired = 1`.

    Usa `min_effective_n=1` SOLO para obtener el IC; el informe lo rotula orientativo si el
    N efectivo real es menor que `MIN_EFFECTIVE_N`.

    Args:
        trades: sombras candidatas.
        main: el veredicto principal.
        measurement_keys: claves de la medicion.
        piloto_keys: claves del piloto.
        unreliable_keys: claves no confiables.
        repaired_keys: claves con JSON reparado.

    Returns:
        El `SensitivityResult` (`robust` es `None` si no hay filas reparadas).
    """
    excluded = repaired_keys & measurement_keys
    if not excluded:
        return SensitivityResult(excluded_groups=0, verdict=None, robust=None)
    verdict = ai_value_verdict(
        trades, min_effective_n=1, piloto_keys=piloto_keys, measurement_keys=measurement_keys,
        unreliable_keys=unreliable_keys | excluded,
    )
    robust = verdict.verdict == main.verdict and verdict.magnitud_baja == main.magnitud_baja
    return SensitivityResult(excluded_groups=len(excluded), verdict=verdict, robust=robust)


def compute_breakdown(
    trades: list[ShadowTrade], bucket_of, *, min_effective_n: int = BREAKDOWN_MIN_EFFECTIVE_N,
) -> list[BucketResult]:
    """Diferencia por cubo (cada operacion en UN solo cubo). Exploratorio: no cambia el veredicto.

    Los conglomerados se recalculan dentro de cada cubo. Un cubo con menos de
    `min_effective_n` conglomerados en algun lado se lista sin intervalo.

    Args:
        trades: sombras ya filtradas.
        bucket_of: funcion que asigna una sombra a su cubo (estrategia o simbolo).
        min_effective_n: minimo por lado para calcular el intervalo.

    Returns:
        Un `BucketResult` por cubo, en orden alfabetico.
    """
    labeled = [
        t for t in trades
        if t.closed_at is not None and t.llm_decision in (APROBADA, RECHAZADA)
    ]
    buckets: dict[str, list[ShadowTrade]] = {}
    for t in labeled:
        buckets.setdefault(bucket_of(t), []).append(t)

    results = []
    for label in sorted(buckets):
        members = buckets[label]
        units = build_units(members)
        n_app = sum(1 for u in units if u.approved_returns)
        n_rej = sum(1 for u in units if u.rejected_returns)
        diff = lo = hi = None
        if n_app >= min_effective_n and n_rej >= min_effective_n:
            boot = cluster_bootstrap_difference(units)
            if boot is not None:
                diff, lo, hi = boot
        results.append(BucketResult(
            label=label, n_trades=len(members), n_eff_approved=n_app, n_eff_rejected=n_rej,
            n_mixed=sum(1 for t in members if len(t.contributing_strategies) > 1),
            diff=diff, ci_low=lo, ci_high=hi,
        ))
    return results


# --- orquestacion ------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictInputs:
    """Todo lo que el veredicto y el placebo necesitan de la base, con los mismos filtros.

    `trades` son todas las sombras (con resultados) y `filtered` las que sobreviven a los
    tres filtros (hash congelado, sin piloto, sin no confiables).
    """

    metas: list[LogMeta]
    light: list[LightShadow]
    trades: list[ShadowTrade]
    filtered: list[ShadowTrade]
    measurement_keys: set[str]
    piloto_keys: set[str]
    unreliable_keys: set[str]
    repaired_keys: set[str]


def check_frozen_hash_mode(
    conn: sqlite3.Connection, frozen_hash: str, allow_synthetic_for_tests: bool,
) -> None:
    """Un hash distinto del congelado real solo se acepta con la bandera sintetica.

    Y esa bandera solo con una base positivamente sintetica.

    Args:
        conn: conexion de solo lectura.
        frozen_hash: hash que se quiere usar.
        allow_synthetic_for_tests: bandera de pruebas.

    Raises:
        ValueError: si el hash no es el congelado y no se pidio el modo sintetico.
        SyntheticGuardError: si se pidio el modo sintetico y la base no lo es.

    Efectos:
        Lee `llm_logs` (solo en modo sintetico).
    """
    if allow_synthetic_for_tests:
        assert_synthetic_database(conn)
    elif frozen_hash != FROZEN_PROMPT_SHA256:
        raise ValueError("un hash distinto del congelado solo se acepta con la bandera sintetica")


def load_verdict_inputs(conn: sqlite3.Connection, frozen_hash: str) -> VerdictInputs:
    """Carga y filtra las sombras para el veredicto o el placebo. LEE RESULTADOS (PnL).

    Solo llamar con el gate abierto o desde una herramienta que no revele la relacion
    etiqueta-resultado (el placebo).

    Args:
        conn: conexion de solo lectura.
        frozen_hash: hash del prompt de la medicion.

    Returns:
        Los `VerdictInputs`.

    Efectos:
        Lee `llm_logs` y `shadow_trades` (todas las columnas, incluidos PnL, precio de
        salida y motivo de cierre).
    """
    metas = _load_log_metas(conn)
    light = _load_light_shadows(conn)
    measurement_keys = {
        m.key for m in metas if m.fase == FASE_MEDICION and m.prompt_sha256 == frozen_hash
    }
    piloto_keys = {m.key for m in metas if m.fase != FASE_MEDICION}
    unreliable_keys = {s.key for s in light if s.unreliable}
    repaired_keys = {m.key for m in metas if m.json_repaired}
    trades = _load_outcome_trades(conn)
    filtered = _verdict_inputs(trades, measurement_keys, piloto_keys, unreliable_keys)
    return VerdictInputs(
        metas=metas, light=light, trades=trades, filtered=filtered,
        measurement_keys=measurement_keys, piloto_keys=piloto_keys,
        unreliable_keys=unreliable_keys, repaired_keys=repaired_keys,
    )


def build_report(
    conn: sqlite3.Connection, *, frozen_hash: str = FROZEN_PROMPT_SHA256,
    allow_synthetic_for_tests: bool = False,
) -> ReportResult:
    """Construye el informe del veredicto con la guardia del gate de forma ESTRUCTURAL.

    Empieza con `compute_status` (sin PnL) y, si el gate esta cerrado, devuelve sin haber
    ejecutado jamas la consulta que lee resultados. Si el gate abre pero tras excluir
    sombras no confiables y del piloto el N efectivo limpio es menor que
    `MIN_EFFECTIVE_N`, declara "punto de analisis no alcanzado" y NO muestra diferencia,
    intervalo ni desglose.

    Args:
        conn: conexion de solo lectura a una COPIA de la base.
        frozen_hash: hash del prompt de la medicion; por defecto, el congelado.
        allow_synthetic_for_tests: acepta otro hash solo con una base positivamente sintetica.

    Returns:
        El `ReportResult`.

    Raises:
        ValueError: si el hash no es el congelado y no se pidio el modo sintetico.
        SyntheticGuardError: si se pidio el modo sintetico y la base no lo es.

    Efectos:
        Lee `llm_logs`, `shadow_trades` y `signals`; PnL solo con el gate abierto.
    """
    check_frozen_hash_mode(conn, frozen_hash, allow_synthetic_for_tests)

    status = compute_status(conn, frozen_hash=frozen_hash)
    if not is_measurement_gate_open(status):
        return ReportResult(state=STATE_GATE_CLOSED, frozen_hash=frozen_hash, status=status)

    # --- gate abierto: a partir de aqui se pueden leer resultados ---
    inputs = load_verdict_inputs(conn, frozen_hash)
    metas, light, trades, filtered = inputs.metas, inputs.light, inputs.trades, inputs.filtered
    measurement_keys, piloto_keys = inputs.measurement_keys, inputs.piloto_keys
    unreliable_keys, repaired_keys = inputs.unreliable_keys, inputs.repaired_keys
    partition = compute_partition(light, metas, frozen_hash)
    profile = compute_omitted_profile(light, metas, frozen_hash)
    clean_app, clean_rej = clean_effective_n(filtered)

    kept_keys = {t.signal_group_key for t in filtered}
    measured_metas = [m for m in metas if m.key in kept_keys]
    diagnostics = compute_sin_llm_diagnostics(measured_metas)
    repaired_groups = sum(1 for m in measured_metas if m.json_repaired)

    common = dict(
        frozen_hash=frozen_hash, status=status, clean_n_approved=clean_app,
        clean_n_rejected=clean_rej, partition=partition, omitted_profile=profile,
        diagnostics=diagnostics, json_repaired_groups=repaired_groups,
    )
    if clean_app < MIN_EFFECTIVE_N or clean_rej < MIN_EFFECTIVE_N:
        return ReportResult(state=STATE_POINT_NOT_REACHED, **common)

    verdict = ai_value_verdict(
        trades, min_effective_n=MIN_EFFECTIVE_N, delta=DELTA_PCT,
        max_sin_llm_share=MAX_SIN_LLM_SHARE, piloto_keys=piloto_keys,
        measurement_keys=measurement_keys, unreliable_keys=unreliable_keys,
    )
    sensitivity = compute_sensitivity(
        trades, verdict, measurement_keys=measurement_keys, piloto_keys=piloto_keys,
        unreliable_keys=unreliable_keys, repaired_keys=repaired_keys,
    )
    by_strategy: list[BucketResult] = []
    by_symbol: list[BucketResult] = []
    if verdict.verdict == "APORTA_VALOR":
        by_strategy = compute_breakdown(filtered, lambda t: t.strategy or "n/d")
        by_symbol = compute_breakdown(filtered, lambda t: t.symbol)
    return ReportResult(
        state=STATE_VERDICT, verdict=verdict, sensitivity=sensitivity,
        by_strategy=by_strategy, by_symbol=by_symbol, **common,
    )


# --- texto --------------------------------------------------------------------------------


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/d" if value is None else f"{value:.{digits}%}"


def _ci(lo: float | None, hi: float | None) -> str:
    return "n/d" if lo is None or hi is None else f"[{lo:.2%}, {hi:.2%}]"


def _render_gate_closed(result: ReportResult) -> list[str]:
    s = result.status
    return [
        "=== Veredicto de valor de la IA: GATE CERRADO ===",
        "",
        f"La medicion aun no junto MIN_EFFECTIVE_N={MIN_EFFECTIVE_N} conglomerados efectivos "
        "por lado. NO se muestra ningun resultado (PnL, diferencia, IC ni veredicto): "
        "mirarlos antes del punto de analisis contaminaria la medicion (seccion i).",
        "",
        f"N efectivo APROBADA:  {s.n_eff_approved}/{MIN_EFFECTIVE_N} "
        f"(faltan {s.shortfall('APROBADA')})",
        f"N efectivo RECHAZADA: {s.n_eff_rejected}/{MIN_EFFECTIVE_N} "
        f"(faltan {s.shortfall('RECHAZADA')})",
    ]


def _render_partition(result: ReportResult) -> list[str]:
    p = result.partition
    assert p is not None
    lines = [
        "## Seccion (m): particion de todos los grupos sombra y omitidos",
        "",
        f"- Total de grupos sombra en la copia: {p.total_groups}",
        f"- Medicion bajo el hash congelado: {p.measurement} "
        f"(de ellos {p.measurement_unreliable} con reconciliacion fallida, "
        "EXCLUIDOS del veredicto)",
        f"- Piloto (fase PILOTO), excluidos de la medicion: {p.piloto}",
        f"- Medicion bajo otro hash de prompt, excluidos: {p.other_hash}",
        f"- Omitidos SIN fila en llm_logs, ANTERIORES a la activacion: "
        f"{p.omitted_pre_activation} (excluidos del veredicto)",
        f"- Omitidos SIN fila en llm_logs, POSTERIORES a la activacion: "
        f"{p.omitted_post_activation} (excluidos del veredicto; demora > 1800 s, sombra ya "
        "cerrada antes de etiquetar o hash no coincidente en ese ciclo -- la causa exacta "
        "no se persiste)",
        "",
    ]
    prof = result.omitted_profile
    if prof is not None and p.omitted_post_activation:
        lines.append("Perfil de los omitidos posteriores frente a los medidos (cantidades):")
        for title, measured, omitted in (
            ("lado", prof.measured_by_side, prof.omitted_by_side),
            ("estrategia", prof.measured_by_strategy, prof.omitted_by_strategy),
        ):
            for label in sorted(set(measured) | set(omitted)):
                lines.append(
                    f"  - {title} {label}: medidos {measured.get(label, 0)}, "
                    f"omitidos {omitted.get(label, 0)}"
                )
        lines.append("")
    return lines


def _render_diagnostics(result: ReportResult) -> list[str]:
    d = result.diagnostics
    assert d is not None
    lines = ["## Seccion (m): SIN_LLM por causa, franja horaria y volatilidad", ""]
    if d.groups_with_log == 0:
        return lines + ["Sin grupos medidos.", ""]
    lines.append(
        f"- SIN_LLM (filas no OK) sobre {d.groups_with_log} grupos medidos: {d.sin_llm_rows} "
        f"({_pct(d.sin_llm_rows / d.groups_with_log)})"
    )
    if d.sin_llm_by_cause:
        for cause, n in sorted(d.sin_llm_by_cause.items()):
            lines.append(f"  - {cause}: {n} ({_pct(n / d.groups_with_log)})")
    else:
        lines.append("  - ninguna causa: 0 filas no OK")
    for title, strata in (
        ("franja horaria UTC", d.by_hour_bin),
        ("tercil de atr_pct", d.by_atr_tercile),
    ):
        if not strata:
            continue
        lines.append(f"- Distribucion por {title} (todos los grupos | SIN_LLM | cociente):")
        for st in strata:
            ratio = "n/d" if st.ratio is None else f"{st.ratio:.2f}"
            lines.append(
                f"  - {st.label}: {_pct(st.all_share)} | {_pct(st.sin_llm_share)} | {ratio}"
            )
    lines.append("")
    return lines


def _render_verdict(result: ReportResult) -> list[str]:
    v = result.verdict
    assert v is not None
    marca = " (MAGNITUD_BAJA)" if v.magnitud_baja else ""
    return [
        "## Veredicto (bootstrap por conglomerados, regla de la seccion i)",
        "",
        f"- Aprobadas: N bruto {v.n_raw_approved}, N efectivo {v.n_eff_approved}",
        f"- Rechazadas: N bruto {v.n_raw_rejected}, N efectivo {v.n_eff_rejected}",
        f"- Diferencia de esperanza APROBADA - RECHAZADA (% del margen): {_pct(v.diff, 2)}, "
        f"IC 95 % {_ci(v.ci_low, v.ci_high)}",
        f"- SIN_LLM: {v.sin_llm_count} de {v.total_groups} grupos ({_pct(v.sin_llm_share)}; "
        f"tope {MAX_SIN_LLM_SHARE:.0%})",
        f"- δ = {DELTA_PCT:.0%} del margen por operacion",
        f"- **Veredicto: {v.verdict}{marca}** ({v.reason})",
        "",
    ]


def _render_sensitivity(result: ReportResult) -> list[str]:
    s = result.sensitivity
    assert s is not None
    lines = ["## Sensibilidad: sin las filas con JSON reparado (json_repaired = 1)", ""]
    if s.verdict is None:
        return lines + ["No hay filas con JSON reparado entre los grupos medidos: coincide "
                        "con el veredicto principal por construccion.", ""]
    v = s.verdict
    provisional = (
        "" if min(v.n_eff_approved, v.n_eff_rejected) >= MIN_EFFECTIVE_N
        else f" -- ORIENTATIVO: N efectivo < {MIN_EFFECTIVE_N} en algun lado"
    )
    marca = " (MAGNITUD_BAJA)" if v.magnitud_baja else ""
    lines += [
        f"- Grupos excluidos por JSON reparado: {s.excluded_groups}",
        f"- N efectivo sin ellos: {v.n_eff_approved} aprobadas, {v.n_eff_rejected} rechazadas"
        f"{provisional}",
        f"- Diferencia (% del margen): {_pct(v.diff, 2)}, IC 95 % {_ci(v.ci_low, v.ci_high)}",
        f"- Categoria sin ellos: {v.verdict}{marca}",
        f"- **Conclusion: {'ROBUSTO' if s.robust else 'SENSIBLE'}** a las filas reparadas "
        f"(principal: {result.verdict.verdict if result.verdict else 'n/d'}).",
        "",
    ]
    return lines


def _render_bucket_table(title: str, buckets: list[BucketResult]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| cubo | operaciones | N ef. aprob. | N ef. rech. | mixtas | Δ (% margen) | IC 95 % |",
        "|---|---|---|---|---|---|---|",
    ]
    for b in buckets:
        if b.diff is None:
            diff, ci = "N insuficiente", "n/d"
        else:
            diff, ci = _pct(b.diff, 2), _ci(b.ci_low, b.ci_high)
        lines.append(
            f"| {b.label} | {b.n_trades} | {b.n_eff_approved} | {b.n_eff_rejected} | "
            f"{b.n_mixed} | {diff} | {ci} |"
        )
    return lines + [""]


def _render_breakdown(result: ReportResult) -> list[str]:
    lines = [
        "## Desglose por estrategia y por simbolo (solo con APORTA_VALOR)",
        "",
        "EXPLORATORIO: no cambia el veredicto, sin correccion por comparaciones multiples; "
        f"un cubo con menos de {BREAKDOWN_MIN_EFFECTIVE_N} conglomerados en algun lado no "
        "lleva IC. Cada operacion cae en un solo cubo (estrategia representativa = la que "
        "fija los niveles de la operacion).",
        "",
    ]
    lines += _render_bucket_table("Por estrategia representativa", result.by_strategy)
    lines += _render_bucket_table("Por simbolo", result.by_symbol)
    return lines


def render_report(result: ReportResult) -> str:
    """El informe de texto del veredicto.

    Con el gate cerrado solo muestra cuanto falta; sin el punto de analisis limpio no
    muestra ninguna diferencia.

    Args:
        result: el `ReportResult`.

    Returns:
        El texto del informe (es lo que `scripts/ai_value_report.py --out` guarda como
        registro oficial).
    """
    if result.state == STATE_GATE_CLOSED:
        return "\n".join(_render_gate_closed(result)) + "\n"

    s = result.status
    lines = [
        "=== Veredicto de valor de la IA (subfase 3.6) ===",
        f"PROMPT_SHA256 de la medicion: {result.frozen_hash}",
        f"Instante mas reciente en la copia: {s.data_now.isoformat() if s.data_now else 'n/d'}",
        f"N efectivo segun el gate: APROBADA {s.n_eff_approved}, RECHAZADA {s.n_eff_rejected} "
        f"(minimo {MIN_EFFECTIVE_N} por lado)",
        f"N efectivo LIMPIO (tras excluir no confiables y piloto): APROBADA "
        f"{result.clean_n_approved}, RECHAZADA {result.clean_n_rejected}",
        "",
    ]
    if result.state == STATE_POINT_NOT_REACHED:
        lines += [
            "PUNTO DE ANALISIS NO ALCANZADO tras las exclusiones: el gate esta abierto, pero "
            f"el N efectivo limpio es menor que {MIN_EFFECTIVE_N} en algun lado. NO se muestra "
            "ninguna diferencia, IC ni veredicto; se calcularan cuando ambos lados lleguen.",
            "",
        ]
        lines += _render_partition(result) + _render_diagnostics(result)
        return "\n".join(lines) + "\n"

    lines += _render_verdict(result)
    lines += _render_partition(result)
    lines += _render_diagnostics(result)
    lines += _render_sensitivity(result)
    if result.verdict is not None and result.verdict.verdict == "APORTA_VALOR":
        lines += _render_breakdown(result)
    lines += PROCEDURE_REMINDER
    return "\n".join(lines) + "\n"


PROCEDURE_REMINDER = [
    "## Orden del procedimiento (decision del 2026-10-17, tomada antes de ver resultados)",
    "",
    "Esta salida solo vale si se obtuvo asi (docs/VEREDICTO_3_6.md):",
    "1. Copia de la base al abrirse el gate.",
    "2. Placebo oficial sobre ESA MISMA copia (scripts/placebo_check.py, 1.000 repeticiones).",
    "3. Placebo NO INFORMATIVO (< 50 % de las repeticiones llega al punto de analisis): NO "
    "se corre el veredicto; se esperan 7 dias mas de datos y se repite desde (1).",
    "4. Placebo CALIBRADO: este informe, una sola vez, con --out y sobre la misma copia. "
    "Guarda esta salida completa como registro oficial; volver a correr con mas datos no es "
    "un veredicto nuevo.",
    "5. Placebo NO calibrado: el criterio no se usa para decidir y se revisa el diseno. "
    "Descarta esta salida.",
    "La espera depende solo de conteos, nunca de resultados.",
    "",
    "Si el placebo de esta copia no salio CALIBRADO, o este informe no se corrio sobre la "
    "misma copia, esta salida NO es el veredicto oficial. Ademas: la sombra entra al precio "
    "de cierre de la vela, sin demora ni slippage (no comparable con la cuenta real).",
]
