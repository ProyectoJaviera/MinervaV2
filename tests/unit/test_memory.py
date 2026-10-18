"""Fase 4, memoria de lecciones: cortes de las caracteristicas, intervalo de Wilson, estados de una
leccion (muestra minima / significancia / estabilidad), construccion de la memoria solo con
operaciones CERRADAS y fiables, recuperacion SIN fuga temporal, puerta de resultados, post-mortem,
calibracion y el ejecutor aislado. Datos sinteticos."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.memory import calibration, entries, features, gate, lessons, postmortem, retrieval
from app.memory.features import QueryFeatures
from app.memory.runtime import (
    LessonsRunner,
    build_lessons_runner,
    maybe_start_lessons_runner,
    run_isolated,
)
from app.persistence.database import Database
from app.persistence.repositories import memory_repo, news_repo
from app.persistence.repositories.memory_repo import MemoryEntry
from app.persistence.repositories.news_repo import NewsRecord
from tests.unit.synthetic_measurement import BASE, Group, create_schema, insert_groups

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


def entry(
    n: int, *, outcome="WIN", ret=5.0, strategy="ema_cross_9_21", side="LONG", symbol="BTCUSDT",
    closed=None, hour=8, atr=1.0, rr=1.5, sl=30.0, label="APROBADA", sentiment=None,
    fear_greed=None, timeframe="4h",
) -> MemoryEntry:
    closed = closed or T0 + n * H
    return MemoryEntry(
        signal_group_key=f"{symbol}|{side}|{n}", shadow_trade_id=n, symbol=symbol, side=side,
        strategy_set=strategy, timeframe=timeframe, decision_time=closed - 3 * H, closed_at=closed,
        hour_utc=hour, atr_pct=atr, risk_reward=rr, sl_margin_loss_pct=sl, llm_label=label,
        news_sentiment=sentiment, news_count=None, fear_greed=fear_greed,
        pnl_net_usdt=ret / 100 * 5.0, margin_usdt=5.0, return_pct=ret, outcome=outcome,
        close_reason="TP" if outcome == "WIN" else "SL", duration_hours=3.0,
    )


# --- caracteristicas ------------------------------------------------------------------------------


@pytest.mark.parametrize(("hour", "expected"), [
    (0, "00-03"), (3, "00-03"), (4, "04-07"), (23, "20-23"), (None, "UNK"),
])
def test_hour_bins(hour, expected):
    assert features.hour_bin(hour) == expected


@pytest.mark.parametrize(("atr", "expected"), [
    (0.79, "ATR<0.8"), (0.8, "ATR0.8-1.6"), (1.59, "ATR0.8-1.6"), (1.6, "ATR>=1.6"), (None, "UNK"),
])
def test_atr_buckets_are_closed_on_the_left(atr, expected):
    assert features.atr_bucket(atr) == expected


@pytest.mark.parametrize(("rr", "expected"), [
    (0.99, "RR<1"), (1.0, "RR1-2"), (1.99, "RR1-2"), (2.0, "RR>=2"), (None, "UNK"),
])
def test_rr_buckets(rr, expected):
    assert features.rr_bucket(rr) == expected


@pytest.mark.parametrize(("sl", "expected"), [
    (19.9, "SL<20"), (20.0, "SL20-35"), (34.9, "SL20-35"), (35.0, "SL>=35"), (None, "UNK"),
])
def test_risk_buckets(sl, expected):
    assert features.risk_bucket(sl) == expected


@pytest.mark.parametrize(("value", "expected"), [
    (-0.15, "NEG"), (-0.149, "NEU"), (0.0, "NEU"), (0.149, "NEU"), (0.15, "POS"), (None, "UNK"),
])
def test_sentiment_buckets(value, expected):
    assert features.sentiment_bucket(value) == expected


@pytest.mark.parametrize(("value", "expected"), [
    (0, "FEAR"), (40, "FEAR"), (41, "NEUTRAL"), (59, "NEUTRAL"), (60, "GREED"), (None, "UNK"),
])
def test_fear_greed_buckets(value, expected):
    assert features.fear_greed_bucket(value) == expected


def test_risk_reward_from_the_planned_levels():
    assert features.risk_reward(100.0, 97.0, 106.0) == pytest.approx(2.0)
    assert features.risk_reward(100.0, 103.0, 94.0) == pytest.approx(2.0)  # SHORT
    assert features.risk_reward(100.0, None, 106.0) is None
    assert features.risk_reward(100.0, 100.0, 106.0) is None  # sin riesgo: indefinido
    assert features.risk_reward(0.0, 97.0, 106.0) is None


def test_patterns_of_an_entry_and_of_a_query_use_the_same_cuts():
    e = entry(1, strategy="donchian_breakout_20+ema_cross_9_21", side="SHORT", hour=13, atr=2.0,
              rr=2.5, sl=40.0, sentiment=-0.4, fear_greed=20)
    q = QueryFeatures(
        symbol="BTCUSDT", side="SHORT",
        strategies=frozenset({"ema_cross_9_21", "donchian_breakout_20"}),
        timeframe="4h", hour_utc=13, atr_pct=2.0, risk_reward=2.5, sl_margin_loss_pct=40.0,
        news_sentiment=-0.4, fear_greed=20,
    )

    pe, pq = features.patterns_of(e), features.patterns_of_query(q)

    assert "llm_label" in pe and "llm_label" not in pq  # la etiqueta es lo que se decide
    for dimension, pattern in pq.items():
        assert pe[dimension] == pattern, dimension
    assert pe["strategy_set+side"] == "donchian_breakout_20+ema_cross_9_21|SHORT"


# --- Wilson --------------------------------------------------------------------------------------


def test_wilson_interval_known_values_and_edges():
    lo, hi = lessons.wilson_interval(50, 100, z=1.96)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert lessons.wilson_interval(0, 0) == (0.0, 1.0)
    lo0, hi0 = lessons.wilson_interval(0, 30)
    assert lo0 == 0.0 and 0.0 < hi0 < 0.25
    lo1, hi1 = lessons.wilson_interval(30, 30)
    assert hi1 == 1.0 and 0.75 < lo1 < 1.0
    # el 99 % es mas ancho que el 95 %
    wide = lessons.wilson_interval(60, 100)
    narrow = lessons.wilson_interval(60, 100, z=1.96)
    assert wide[0] < narrow[0] and wide[1] > narrow[1]


# --- estados de una leccion ----------------------------------------------------------------------


def dataset(groups):
    """`groups`: lista de (strategy, [(n_total, n_wins) por mitad]) -> entradas intercaladas en el
    tiempo. La primera mitad del tiempo recibe el primer elemento de cada lista."""
    out: list[MemoryEntry] = []
    counter = 0
    for half, _ in enumerate(("primera", "segunda")):
        for strategy, per_half in groups:
            n_total, n_wins = per_half[half]
            for i in range(n_total):
                counter += 1
                closed = T0 + half * 1000 * H + counter * H
                out.append(entry(
                    counter, strategy=strategy, outcome="WIN" if i < n_wins else "LOSS",
                    ret=5.0 if i < n_wins else -5.0, closed=closed,
                ))
    return out


def status_of(rows, dimension, pattern):
    return next(x.status for x in rows if x.dimension == dimension and x.pattern == pattern)


def test_an_active_lesson_needs_sample_significance_and_stability():
    data = dataset([
        ("good", [(30, 25), (30, 25)]),       # 83 % en las dos mitades
        ("bad", [(50, 20), (50, 20)]),        # 40 %
        ("tiny", [(5, 5), (5, 5)]),           # 100 % pero solo 10 operaciones
    ])

    rows = lessons.build_lessons(data, min_sample=30)

    assert status_of(rows, "strategy_set", "good") == lessons.ACTIVE
    assert status_of(rows, "strategy_set", "tiny") == lessons.INSUFFICIENT
    good = next(x for x in rows if x.dimension == "strategy_set" and x.pattern == "good")
    assert good.n == 60 and good.wins == 50 and good.winrate == pytest.approx(50 / 60)
    assert good.ci_low > good.base_winrate  # el intervalo excluye la tasa base
    assert good.half1_delta > 0 and good.half2_delta > 0
    assert good.mean_return_pct == pytest.approx((50 * 5.0 - 10 * 5.0) / 60)


def test_a_pattern_whose_interval_contains_the_base_rate_is_not_significant():
    data = dataset([("a", [(40, 20), (40, 20)]), ("b", [(40, 20), (40, 20)])])

    rows = lessons.build_lessons(data, min_sample=30)

    assert status_of(rows, "strategy_set", "a") == lessons.NOT_SIGNIFICANT
    assert status_of(rows, "side", "LONG") == lessons.NOT_SIGNIFICANT


def test_a_significant_pattern_that_flips_sign_between_halves_is_unstable():
    """El patron acierta 78 % en las dos mitades, pero la mitad 1 es mala para todos (base ~35 %) y
    la 2 buena (base ~80 %): en la primera 'gana' a la base y en la segunda no."""
    data = dataset([
        ("others", [(300, 90), (300, 240)]),
        ("g", [(30, 23), (30, 23)]),
    ])

    rows = lessons.build_lessons(data, min_sample=30)

    g = next(x for x in rows if x.dimension == "strategy_set" and x.pattern == "g")
    assert g.ci_low > g.base_winrate  # es significativa en conjunto...
    assert g.half1_delta > 0 > g.half2_delta  # ...pero el signo cambia
    assert g.status == lessons.UNSTABLE


def test_unknown_values_never_form_a_pattern_and_empty_input_gives_nothing():
    e = entry(1, atr=None, rr=None, sl=None, hour=None, timeframe=None)
    e.hour_utc = None

    rows = lessons.build_lessons([e], min_sample=1)
    patterns = {(x.dimension, x.pattern) for x in rows}

    assert ("atr_bucket", "UNK") not in patterns and ("hour_bin", "UNK") not in patterns
    assert ("side", "LONG") in patterns
    assert lessons.build_lessons([], min_sample=30) == []


def test_build_lessons_is_deterministic():
    data = dataset([("a", [(40, 30), (40, 28)]), ("b", [(40, 12), (40, 14)])])

    assert lessons.build_lessons(data, min_sample=30) == lessons.build_lessons(
        list(reversed(data)), min_sample=30
    )


# --- construccion de la memoria desde la base ----------------------------------------------------


@pytest.fixture
async def memory_db(tmp_path):
    path = tmp_path / "memory.db"
    await create_schema(path)
    return path


def closed_group(key, n, *, ret=0.2, label="APROBADA", symbol="BTCUSDT", side="LONG",
                 strategy="ema_cross_9_21", **kw):
    opened = BASE + timedelta(hours=10 * n)
    return Group(
        key=key, opened_at=opened, closed_at=opened + 2 * H, ret=ret, label=label, symbol=symbol,
        side=side, strategy=strategy, contributing=(strategy,), **kw,
    )


async def open_db(path) -> Database:
    db = Database(str(path))
    await db.connect()
    return db


async def test_only_closed_reliable_operations_enter_the_memory(memory_db):
    open_group = Group(key="abierta", opened_at=BASE, closed_at=None, ret=None)
    broken = closed_group("rota", 2, reconciliation_failed=True, close_reason="RECONCILE_FAILED")
    insert_groups(memory_db, [
        closed_group("g1", 0, ret=0.3), closed_group("g2", 1, ret=-0.4, label="RECHAZADA"),
        open_group, broken,
    ])
    db = await open_db(memory_db)
    try:
        assert await entries.count_closed_reliable(db) == 2
        added = await entries.refresh_entries(db, now=T0)
        again = await entries.refresh_entries(db, now=T0)  # idempotente

        stored = {e.signal_group_key: e for e in await memory_repo.get_entries_closed_before(db)}
    finally:
        await db.close()

    assert (added, again) == (2, 0)
    assert set(stored) == {"g1", "g2"}  # ni la abierta ni la de reconciliacion fallida
    win, loss = stored["g1"], stored["g2"]
    assert win.outcome == "WIN" and win.return_pct == pytest.approx(30.0)
    assert loss.outcome == "LOSS" and loss.llm_label == "RECHAZADA"
    assert loss.return_pct == pytest.approx(-40.0) and loss.pnl_net_usdt == pytest.approx(-2.0)
    assert win.strategy_set == "ema_cross_9_21" and win.atr_pct == 1.0
    assert win.decision_time == BASE and win.hour_utc == 0
    assert win.duration_hours == pytest.approx(2.0)


async def test_news_and_fear_greed_in_the_entry_respect_the_decision_instant(memory_db):
    insert_groups(memory_db, [closed_group("g1", 5, ret=0.1)])  # decision en BASE+50h
    decision = BASE + timedelta(hours=50)
    db = await open_db(memory_db)
    try:
        def rec(n, *, fetched, published, sentiment, assets=("BTC",)):
            return NewsRecord(
                source="gdelt", fetched_at=fetched, published_at=published, title=f"t{n}",
                url=f"https://x.test/{n}", url_hash=f"u{n}", title_hash=f"h{n}",
                assets=list(assets), sentiment=sentiment,
            )

        await news_repo.insert_item(db, rec(1, fetched=decision - H, published=decision - H,
                                            sentiment=0.6))
        await news_repo.insert_item(db, rec(2, fetched=decision - 2 * H, published=decision - 2 * H,
                                            sentiment=0.2))
        # DESPUES de la decision: no debe entrar a la entrada
        await news_repo.insert_item(db, rec(3, fetched=decision + H, published=decision - H,
                                            sentiment=-0.9))
        await news_repo.insert_item(db, rec(4, fetched=decision - H, published=decision + H,
                                            sentiment=-0.9))
        await news_repo.upsert_fear_greed(db, ts=decision - 5 * H, value=30,
                                          classification="Fear", fetched_at=decision - 4 * H)
        await news_repo.upsert_fear_greed(db, ts=decision + H, value=90,
                                          classification="Extreme Greed",
                                          fetched_at=decision + 2 * H)
        await entries.refresh_entries(db, now=T0)
        [stored] = await memory_repo.get_entries_closed_before(db)
    finally:
        await db.close()

    assert stored.news_count == 2 and stored.news_sentiment == pytest.approx(0.4)
    assert stored.fear_greed == 30


@pytest.mark.parametrize(("atr_column", "prompt", "expected"), [
    (1.7, 'S\n\n{"volatility_atr14_pct": 0.5}', 1.7),  # si la columna esta, manda ella
    (None, 'SISTEMA con {llaves}\n\n{"a": 1, "volatility_atr14_pct": 0.93}', 0.93),
    (None, 'S\n\n{"volatility_atr14_pct": 2}', 2.0),
    (None, 'S\n\n{"volatility_atr14_pct": null}', None),
    (None, 'S\n\n{"volatility_atr14_pct": true}', None),
    (None, 'S\n\n{"volatility_atr14_pct": "alto"}', None),
    (None, 'S\n\n{roto', None),
    (None, "sin mensaje", None),
    (None, "", None),
    (None, None, None),
])
def test_atr_is_read_from_the_logged_prompt_when_the_column_is_empty(atr_column, prompt, expected):
    assert entries.atr_from_log({"atr_pct": atr_column, "prompt": prompt}) == expected


def test_without_a_log_row_there_is_no_atr():
    assert entries.atr_from_log(None) is None


async def test_the_entry_takes_the_atr_the_model_saw_from_the_measurement_log(memory_db):
    """El etiquetador en vivo no rellena `llm_logs.atr_pct`: el ATR sale del prompt registrado."""
    insert_groups(memory_db, [
        closed_group("con-prompt", 0, atr_pct=None), closed_group("sin-nada", 1, atr_pct=None),
    ])
    db = await open_db(memory_db)
    try:
        await db.execute(
            "UPDATE llm_logs SET prompt = ? WHERE signal_group_key = 'con-prompt'",
            ('SISTEMA\n\n{"symbol": "AAAUSDT", "volatility_atr14_pct": 1.234}',),
        )
        await entries.refresh_entries(db, now=T0)
        stored = {e.signal_group_key: e for e in await memory_repo.get_entries_closed_before(db)}
    finally:
        await db.close()

    assert stored["con-prompt"].atr_pct == pytest.approx(1.234)
    assert stored["sin-nada"].atr_pct is None  # prompt 'p' de la siembra: ilegible, no se inventa


# --- recuperacion --------------------------------------------------------------------------------


def query(**kw) -> QueryFeatures:
    base = dict(
        symbol="BTCUSDT", side="LONG", strategies=frozenset({"ema_cross_9_21"}), timeframe="4h",
        hour_utc=8, atr_pct=1.0, risk_reward=1.5, sl_margin_loss_pct=30.0,
    )
    base.update(kw)
    return QueryFeatures(**base)


def test_similarity_is_one_for_identical_features_and_none_across_sides():
    same = entry(1)

    assert retrieval.similarity(query(), same) == pytest.approx(1.0)
    assert retrieval.similarity(query(side="SHORT"), same) is None


def test_similarity_drops_with_each_difference_and_ignores_unknown_query_fields():
    base = entry(1)
    other_symbol = entry(2, symbol="ETHUSDT")
    other_strategy = entry(3, strategy="donchian_breakout_20")
    full = retrieval.similarity(query(), base)

    score_symbol = retrieval.similarity(query(), other_symbol)
    score_strategy = retrieval.similarity(query(), other_strategy)
    assert score_symbol < full
    assert score_strategy < score_symbol  # la estrategia pesa mas que el simbolo
    sparse = query(timeframe=None, hour_utc=None, atr_pct=None, risk_reward=None,
                   sl_margin_loss_pct=None)
    assert retrieval.similarity(sparse, base) == pytest.approx(1.0)  # solo cuenta lo que se sabe
    half = retrieval.similarity(
        query(strategies=frozenset({"ema_cross_9_21", "donchian_breakout_20"})), base
    )
    assert half < full  # Jaccard 1/2


def test_find_similar_never_returns_operations_that_had_not_closed_yet():
    as_of = T0 + 10 * H
    data = [
        entry(1, closed=as_of - H), entry(2, closed=as_of),  # cierra EXACTAMENTE en as_of
        entry(3, closed=as_of + H),
    ]

    result = retrieval.find_similar(query(), data, as_of=as_of, k=5)

    assert [c.entry.signal_group_key for c in result.cohort] == ["BTCUSDT|LONG|1"]


def test_find_similar_returns_the_best_winners_and_losers_separately():
    as_of = T0 + 1000 * H
    data = [entry(i, outcome="WIN", ret=5.0) for i in range(1, 6)]
    data += [entry(i, outcome="LOSS", ret=-5.0) for i in range(6, 9)]
    data += [entry(9, outcome="WIN", symbol="ETHUSDT")]  # parecida pero de otro simbolo

    result = retrieval.find_similar(query(), data, as_of=as_of, k=2)

    assert len(result.winners) == 2 and len(result.losers) == 2
    assert all(c.entry.outcome == "WIN" for c in result.winners)
    assert all(c.entry.symbol == "BTCUSDT" for c in result.winners)  # las del mismo simbolo primero
    assert len(result.cohort) == 9


def settings_with(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


async def test_lessons_context_is_never_built_while_the_results_are_hidden(db):
    await memory_repo.insert_entry(db, entry(1), built_at=T0)

    hidden = await retrieval.build_lessons_context(
        db, settings_with(), signal_group_key="k", query=query(), as_of=T0 + 100 * H,
        reveal_allowed=False,
    )

    assert hidden is None
    assert await memory_repo.count_applications(db) == 0  # y ni siquiera se registra


async def test_lessons_context_has_low_evidence_below_the_minimum_sample_and_no_rates(db):
    for i in range(1, 6):
        await memory_repo.insert_entry(
            db, entry(i, outcome="WIN" if i % 2 else "LOSS", ret=5.0 if i % 2 else -5.0),
            built_at=T0,
        )

    context = await retrieval.build_lessons_context(
        db, settings_with(LESSONS_MIN_SAMPLE=30), signal_group_key="k1", query=query(),
        as_of=T0 + 100 * H, reveal_allowed=True,
    )

    assert context["evidence"] == "BAJA" and context["n_similar"] == 5
    assert "winrate" not in context and "mean_return_pct" not in context
    assert context["examples_are_anecdotal"] is True
    assert context["winners"] and context["losers"]
    assert "no garantiza" in context["note"]


async def test_lessons_context_gives_the_cohort_statistics_with_enough_sample(db):
    for i in range(1, 11):
        await memory_repo.insert_entry(
            db, entry(i, outcome="WIN" if i <= 7 else "LOSS", ret=4.0 if i <= 7 else -6.0),
            built_at=T0,
        )

    context = await retrieval.build_lessons_context(
        db, settings_with(LESSONS_MIN_SAMPLE=10, LESSONS_TOP_K=2), signal_group_key="k2",
        query=query(), as_of=T0 + 100 * H, reveal_allowed=True,
    )

    assert context["evidence"] == "SUFICIENTE" and context["n_similar"] == 10
    assert context["winrate"] == 0.7 and context["mean_return_pct"] == pytest.approx(1.0)
    lo, hi = context["winrate_ci99"]
    assert lo < 0.7 < hi
    assert len(context["winners"]) == len(context["losers"]) == 2


async def test_lessons_context_only_uses_what_had_closed_before_the_decision(db):
    for i in range(1, 6):
        await memory_repo.insert_entry(db, entry(i, closed=T0 + i * H), built_at=T0)

    context = await retrieval.build_lessons_context(
        db, settings_with(), signal_group_key="k3", query=query(), as_of=T0 + 3 * H,
        reveal_allowed=True, record=False,
    )

    assert context["n_similar"] == 2  # solo las que cerraron en T0+1h y T0+2h (la de +3h no)
    assert await memory_repo.count_applications(db) == 0  # record=False


async def test_it_records_which_active_lessons_were_applied_once_per_decision(db):
    data = dataset([("good", [(30, 25), (30, 25)]), ("bad", [(50, 20), (50, 20)])])
    for e in data:
        await memory_repo.insert_entry(db, e, built_at=T0)
    build_id, rows = await lessons.rebuild_lessons(db, min_sample=30, now=T0)
    assert any(x.status == lessons.ACTIVE for x in rows)
    q = query(strategies=frozenset({"good"}))
    args = dict(signal_group_key="kk", query=q, as_of=T0 + 5000 * H, reveal_allowed=True)

    context = await retrieval.build_lessons_context(db, settings_with(), **args)
    await retrieval.build_lessons_context(db, settings_with(), **args)  # misma decision

    active = {(x["dimension"], x["pattern"]) for x in context["active_lessons"]}
    assert ("strategy_set", "good") in active
    assert all(x["winrate"] > x["base_winrate"] for x in context["active_lessons"]
               if x["pattern"] == "good")
    assert await memory_repo.count_applications(db) == 1  # idempotente por decision


# --- post-mortem ---------------------------------------------------------------------------------


async def insert_trade(db, n, pnl, *, strategy="ema_cross_9_21", symbol="BTCUSDT", side="LONG",
                       reason="SL", opened_hour=8):
    opened = T0.replace(hour=0) + timedelta(days=n, hours=opened_hour)
    await db.execute(
        "INSERT INTO trades (symbol, side, strategy, status, leverage, margin_usdt, notional_usdt, "
        "qty, entry_price, pnl_net_usdt, close_reason, opened_at, closed_at, funding_paid_usdt) "
        "VALUES (?, ?, ?, 'CLOSED', 10, 5, 50, 1, 100, ?, ?, ?, ?, 0.01)",
        (symbol, side, strategy, pnl, reason, opened.isoformat(),
         (opened + 3 * H).isoformat()),
    )


async def test_a_losing_streak_produces_one_deterministic_postmortem(db):
    await insert_trade(db, 0, +1.0)
    for n in (1, 2, 3):
        await insert_trade(db, n, -1.0, symbol="BTCUSDT", side="LONG")
    await insert_trade(db, 4, -2.0, symbol="ETHUSDT", side="LONG")

    body = await postmortem.build_postmortem(db, n_losses=4)

    assert body["losses"] == 4 and body["total_pnl_net_usdt"] == pytest.approx(-5.0)
    assert body["in_common"]["side"] == "LONG" and body["in_common"]["strategy"] == "ema_cross_9_21"
    assert body["in_common"]["close_reason"] == "SL" and body["in_common"]["hour_bin"] == "08-11"
    assert "symbol" not in body["in_common"] or body["in_common"]["symbol"] == "BTCUSDT"
    assert body["mean_duration_hours"] == pytest.approx(3.0)
    assert body["funding_paid_usdt"] == pytest.approx(0.04)

    assert await postmortem.maybe_postmortem(db, n_losses=4, now=T0) is True
    assert await postmortem.maybe_postmortem(db, n_losses=4, now=T0) is False  # una por racha
    [report] = await memory_repo.get_reports(db, "POSTMORTEM")
    assert report["body"]["losses"] == 4


async def test_no_postmortem_for_a_short_streak_or_after_a_win(db):
    await insert_trade(db, 0, -1.0)
    await insert_trade(db, 1, -1.0)
    assert await postmortem.build_postmortem(db, n_losses=3) is None  # solo 2 seguidas

    await insert_trade(db, 2, +0.5)  # una ganadora corta la racha
    assert await postmortem.build_postmortem(db, n_losses=2) is None
    assert await postmortem.maybe_postmortem(db, n_losses=2, now=T0) is False
    assert await memory_repo.get_reports(db, "POSTMORTEM") == []


async def test_a_postmortem_ignores_reconciliation_failures_and_open_trades(db):
    await insert_trade(db, 0, -1.0)
    await insert_trade(db, 1, -1.0, reason="RECONCILE_FAILED")
    await db.execute(
        "INSERT INTO trades (symbol, side, status, leverage, margin_usdt, notional_usdt, qty, "
        "entry_price, opened_at) VALUES ('BTCUSDT', 'LONG', 'OPEN', 10, 5, 50, 1, 100, ?)",
        (T0.isoformat(),),
    )

    assert await postmortem.build_postmortem(db, n_losses=2) is None


# --- calibracion ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [
    ('{"decision": "APROBAR", "confianza": 0.83}', 0.83), ('{"confianza": 1}', 1.0),
    ('{"confianza": 1.5}', None), ('{"confianza": true}', None), ('{"confianza": "alta"}', None),
    ("no es json", None), ("", None), (None, None), ("[1, 2]", None),
])
def test_confidence_parsing_never_raises(raw, expected):
    assert calibration.confidence_of(raw) == expected


async def test_the_calibration_table_groups_decisions_by_confidence(memory_db):
    groups = [
        closed_group(f"a{i}", i, ret=0.3 if i < 6 else -0.3, label="APROBADA") for i in range(10)
    ] + [closed_group("r0", 20, ret=-0.2, label="RECHAZADA")]
    insert_groups(memory_db, groups)
    db = await open_db(memory_db)
    try:
        for i in range(10):
            raw = json.dumps({"decision": "APROBAR", "confianza": 0.85 if i < 8 else 0.55})
            await db.execute(
                "UPDATE llm_logs SET response_raw = ? WHERE signal_group_key = ?", (raw, f"a{i}"),
            )
        await db.execute(
            "UPDATE llm_logs SET response_raw = ? WHERE signal_group_key = 'r0'",
            ('{"decision": "RECHAZAR", "confianza": 0.7}',),
        )
        await entries.refresh_entries(db, now=T0)

        table = await calibration.calibration_table(db)
        saved = await calibration.save_calibration(db, now=T0)
        again = await calibration.save_calibration(db, now=T0)
    finally:
        await db.close()

    approved = {row["bucket"]: row for row in table["table"]["APROBAR"]}
    high, low = approved["0.80-0.90"], approved["0.50-0.60"]
    assert high["n"] == 8 and high["winrate"] == pytest.approx(6 / 8)
    assert low["n"] == 2 and low["winrate"] == 0.0
    assert high["ci99"][0] < 0.75 < high["ci99"][1]
    rejected = {row["bucket"]: row for row in table["table"]["RECHAZAR"]}
    assert rejected["0.70-0.80"]["n"] == 1
    assert table["rows"] == 11 and table["unreadable_confidence"] == 0
    assert saved is True and again is False  # un informe por tamano de memoria


# --- ejecutor aislado ----------------------------------------------------------------------------


async def test_the_runner_builds_everything_once_and_survives_an_empty_database(db):
    runner = LessonsRunner(db, settings_with(LESSONS_MEMORY_ENABLED=True))

    summary = await runner.run_once(now=T0)

    assert summary["entries_added"] == 0 and summary["lessons"] == 0
    assert summary["postmortem"] is False and runner.last_run_at == T0


async def test_the_runner_end_to_end_on_synthetic_closed_operations(memory_db):
    insert_groups(
        memory_db, [closed_group(f"g{i}", i, ret=0.2 if i % 3 else -0.2) for i in range(40)]
    )
    db = await open_db(memory_db)
    try:
        runner = LessonsRunner(db, settings_with(LESSONS_MIN_SAMPLE=10))
        first = await runner.run_once(now=T0)
        second = await runner.run_once(now=T0 + timedelta(days=1))
    finally:
        await db.close()

    assert first["entries_added"] == 40 and second["entries_added"] == 0
    assert first["lessons"] > 0 and first["build_id"] != second["build_id"]


def test_it_is_off_by_default_and_building_it_never_raises(db, monkeypatch, caplog):
    assert build_lessons_runner(settings_with(), db) is None

    class Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("fallo con clave=SECRETO")

    monkeypatch.setattr("app.memory.runtime.LessonsRunner", Boom)
    with caplog.at_level(logging.ERROR, logger="app.memory.runtime"):
        assert build_lessons_runner(settings_with(LESSONS_MEMORY_ENABLED=True), db) is None
    assert any("NO iniciada (RuntimeError)" in r.getMessage() for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


async def test_maybe_start_registers_exactly_one_task_only_when_enabled(db):
    tasks: list[asyncio.Task] = []

    assert maybe_start_lessons_runner(settings_with(), db, tasks) is None and tasks == []
    runner = maybe_start_lessons_runner(settings_with(LESSONS_MEMORY_ENABLED=True), db, tasks)

    assert runner is not None and len(tasks) == 1
    runner.stop()
    await asyncio.wait_for(tasks[0], timeout=2)


async def test_a_runner_that_dies_with_a_bug_does_not_propagate(caplog):
    class Exploding:
        async def run_forever(self):
            raise RuntimeError("bug")

    with caplog.at_level(logging.ERROR, logger="app.memory.runtime"):
        await asyncio.wait_for(run_isolated(Exploding()), timeout=1)

    assert any("termino de forma inesperada (RuntimeError)" in r.getMessage()
               for r in caplog.records)


async def test_the_gate_helper_is_the_reveal_condition(db):
    assert await gate.results_visible(db) is False  # sin medicion: oculto
    assert "ocultos hasta el registro oficial" in gate.HIDDEN_RESULTS
