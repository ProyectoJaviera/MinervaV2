"""`app.trading.ai_value_placebo`: calibracion por placebo sobre los MISMOS filtros
que el veredicto. Datos SINTETICOS en una base de archivo con el esquema real,
reabierta en solo lectura."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta

import pytest

from app.trading.ai_value import MIN_EFFECTIVE_N
from app.trading.ai_value_placebo import (
    PlaceboRun,
    build_placebo,
    prob_both_sides_reach,
    render_placebo,
)
from app.trading.ai_value_report import SyntheticGuardError
from tests.unit.synthetic_measurement import (
    BASE,
    SYNTHETIC_HASH,
    Group,
    create_schema,
    insert_groups,
    open_read_only,
    scenario,
)

SMALL_N = 40
RUN_AT = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def small_n(monkeypatch):
    monkeypatch.setattr("app.trading.ai_value_placebo.MIN_EFFECTIVE_N", SMALL_N)
    return SMALL_N


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_placebo.db"
    await create_schema(path)
    return path


def run_placebo(path, n_repeats: int = 10) -> PlaceboRun:
    conn = open_read_only(path)
    try:
        return build_placebo(
            conn, frozen_hash=SYNTHETIC_HASH, allow_synthetic_for_tests=True, n_repeats=n_repeats,
        )
    finally:
        conn.close()


# --- (a) sin efecto real el placebo calibra, a escala real --------------------------


async def test_no_real_effect_at_300_plus_per_side_is_calibrated(db_path):
    groups = scenario(310, 310, approved_ret=0.0, rejected_ret=0.0)
    insert_groups(db_path, groups)

    run = run_placebo(db_path, n_repeats=20)  # constantes REALES: MIN_EFFECTIVE_N = 300

    assert MIN_EFFECTIVE_N == 300
    assert run.n_closed_clusters == 620 and run.approval_rate == pytest.approx(0.5)
    assert run.result.calibrated
    assert run.result.frequency < 0.05
    assert run.expected_valid_probability > 0.5 and run.informative
    text = render_placebo(run, run_at=RUN_AT)
    assert "Calibrado (frecuencia < 5%): SI" in text
    assert "ADVERTENCIA" not in text


async def test_no_real_effect_with_many_repeats_is_calibrated_at_small_n(db_path, small_n):
    insert_groups(db_path, scenario(60, 60, approved_ret=0.0, rejected_ret=0.0))

    run = run_placebo(db_path, n_repeats=100)

    assert run.result.calibrated, (run.result.aporta_valor_count, run.result.n_repeats)


# --- (b) los filtros del veredicto ----------------------------------------------------------


async def test_unreliable_pilot_other_hash_and_omitted_groups_never_enter(db_path, small_n):
    clean = scenario(small_n, small_n, approved_ret=0.0, rejected_ret=0.0)
    insert_groups(db_path, clean)
    baseline = run_placebo(db_path, n_repeats=8)

    def closed(key, **kw):
        t0 = BASE + timedelta(days=500 + len(key))
        return Group(key=key, opened_at=t0, closed_at=t0 + timedelta(hours=1), ret=50.0,
                     symbol=f"X{len(key)}USDT", **kw)

    extras = [
        closed("recon", reconciliation_failed=True),
        closed("pil", fase="PILOTO"),
        closed("otro", prompt_hash="otro-hash"),
        closed("omit", log=False),
    ]
    insert_groups(db_path, extras)
    with_extras = run_placebo(db_path, n_repeats=8)

    assert with_extras.excluded_piloto == 1 and with_extras.excluded_unreliable == 1
    assert with_extras.groups_after_filters == baseline.groups_after_filters == 2 * small_n
    assert with_extras.n_closed_clusters == baseline.n_closed_clusters
    assert with_extras.n_labeled_closed == baseline.n_labeled_closed
    assert with_extras.result == baseline.result  # mismo universo, misma semilla


async def test_a_reconcile_failed_close_reason_is_also_excluded(db_path, small_n):
    clean = scenario(small_n, small_n)
    insert_groups(db_path, clean)
    t0 = BASE + timedelta(days=900)
    insert_groups(db_path, [Group(
        key="rf", opened_at=t0, closed_at=t0 + timedelta(hours=1), ret=0.0,
        close_reason="RECONCILE_FAILED", symbol="RFUSDT",
    )])

    run = run_placebo(db_path, n_repeats=3)

    assert run.excluded_unreliable == 1
    assert run.groups_after_filters == 2 * small_n


# --- probabilidad de llegar al punto de analisis ------------------------------------------------


def test_prob_both_sides_reach_matches_the_exact_binomial_sum():
    # C=10, p=0.5, minimo 3: P(3 <= X <= 7) = (120+210+252+210+120)/1024
    assert prob_both_sides_reach(10, 0.5, 3) == pytest.approx(912 / 1024)
    # asimetrica: C=10, p=0.8, minimo 2: P(2 <= X <= 8)
    exact = sum(math.comb(10, x) * 0.8**x * 0.2 ** (10 - x) for x in range(2, 9))
    assert prob_both_sides_reach(10, 0.8, 2) == pytest.approx(exact)


def test_prob_both_sides_reach_edge_cases():
    assert prob_both_sides_reach(599, 0.5, 300) == 0.0  # no caben 300 por lado
    assert prob_both_sides_reach(1000, 0.0, 300) == 0.0
    assert prob_both_sides_reach(1000, 1.0, 300) == 0.0
    assert 0.0 < prob_both_sides_reach(600, 0.5, 300) < 0.05  # solo X = 300 exacto
    assert prob_both_sides_reach(2000, 0.5, 300) == pytest.approx(1.0)
    assert prob_both_sides_reach(20000, 0.3, 300) == pytest.approx(1.0)  # sin desbordar


# --- poco informativo, nada que calibrar, guardias -------------------------------------------


async def test_few_clusters_are_flagged_as_not_informative(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n, approved_ret=0.0, rejected_ret=0.0))

    run = run_placebo(db_path, n_repeats=5)  # 80 conglomerados: solo X = 40 llega

    assert run.expected_valid_probability < 0.5 and not run.informative
    text = render_placebo(run, run_at=RUN_AT)
    assert "ADVERTENCIA" in text and "poco informativo" in text


async def test_nothing_to_calibrate_without_closed_labeled_groups(db_path, small_n):
    insert_groups(db_path, [Group(key="a", opened_at=BASE, closed_at=None, label="SIN_LLM")])

    run = run_placebo(db_path)

    assert run.result is None and run.approval_rate is None
    assert "nada que calibrar" in render_placebo(run, run_at=RUN_AT)


async def test_placebo_record_block_is_ready_to_copy(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n))

    text = render_placebo(run_placebo(db_path, n_repeats=4), run_at=RUN_AT, elapsed_s=90.0)

    assert "Para copiar al registro de docs/FASE3_6_LLM.md" in text
    assert "**Placebo de la medicion, 2026-10-17 12:00 UTC**" in text
    assert "semilla del placebo 20261009" in text
    assert "Duracion de la corrida: 1.5 min" in text


async def test_placebo_is_read_only_and_deterministic(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n))
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    first = run_placebo(db_path, n_repeats=6)
    second = run_placebo(db_path, n_repeats=6)

    assert first == second
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


async def test_synthetic_guard_applies_to_the_placebo_too(db_path):
    groups = scenario(5, 5)
    groups[0].model = "claude-sonnet-5-5"
    insert_groups(db_path, groups)
    conn = open_read_only(db_path)
    try:
        with pytest.raises(SyntheticGuardError):
            build_placebo(
                conn, frozen_hash=SYNTHETIC_HASH, allow_synthetic_for_tests=True, n_repeats=1,
            )
        with pytest.raises(ValueError, match="bandera sintetica"):
            build_placebo(conn, frozen_hash=SYNTHETIC_HASH, n_repeats=1)
    finally:
        conn.close()
