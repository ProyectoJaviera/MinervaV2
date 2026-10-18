"""Recuperacion de operaciones similares ya CERRADAS y contexto de lecciones para una decision
futura (prompt `signal_review_v5`; nada de esto entra al prompt congelado de la medicion).

Reglas (cada una con test):
- **Sin fuga temporal**: solo entradas cerradas ESTRICTAMENTE antes de `as_of`.
- **Muestra minima**: las estadisticas de la cohorte se entregan solo con
  n >= `LESSONS_MIN_SAMPLE`; con menos, "BAJA" evidencia y solo el conteo. Los ejemplos concretos
  (ganadoras y perdedoras) se entregan siempre que haya, marcados como anecdoticos.
- **Resultados detras de la puerta**: `build_lessons_context` devuelve `None` salvo que los
  resultados sean visibles (`reveal_allowed`): antes del registro oficial no hay lecciones, ni en
  un prompt.
- Se registra que lecciones se aplicaron a cada decision (`lesson_applications`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Settings
from app.memory.features import (
    QueryFeatures,
    atr_bucket,
    fear_greed_bucket,
    hour_bin,
    patterns_of_query,
    risk_bucket,
    rr_bucket,
    sentiment_bucket,
)
from app.memory.lessons import ACTIVE, wilson_interval
from app.persistence.database import Database
from app.persistence.repositories import memory_repo
from app.persistence.repositories.memory_repo import MemoryEntry

EVIDENCE_LOW = "BAJA"
EVIDENCE_OK = "SUFICIENTE"
DEFAULT_MIN_SCORE = 0.5

# (nombre, peso). Un dato desconocido en la consulta no puntua; uno desconocido en la entrada cuenta
# como no coincidencia.
_WEIGHTS = {
    "strategies": 3.0, "symbol": 1.0, "timeframe": 1.0, "hour_bin": 1.0, "atr_bucket": 1.0,
    "rr_bucket": 1.0, "risk_bucket": 0.5, "sentiment_bucket": 1.0, "fear_greed_bucket": 0.5,
}


@dataclass(frozen=True)
class SimilarCase:
    score: float
    entry: MemoryEntry


def similarity(query: QueryFeatures, entry: MemoryEntry) -> float | None:
    """Parecido en [0, 1] entre la operacion nueva y una pasada. `None` si el lado es distinto (un
    LONG no se compara con un SHORT)."""
    if query.side != entry.side:
        return None
    total = earned = 0.0

    def add(name: str, matched: bool) -> None:
        nonlocal total, earned
        total += _WEIGHTS[name]
        earned += _WEIGHTS[name] if matched else 0.0

    union = query.strategies | entry.strategies
    jaccard = len(query.strategies & entry.strategies) / len(union) if union else 0.0
    total += _WEIGHTS["strategies"]
    earned += _WEIGHTS["strategies"] * jaccard
    add("symbol", query.symbol == entry.symbol)
    if query.timeframe is not None:
        add("timeframe", query.timeframe == entry.timeframe)
    if query.hour_utc is not None:
        add("hour_bin", hour_bin(query.hour_utc) == hour_bin(entry.hour_utc))
    if query.atr_pct is not None:
        add("atr_bucket", atr_bucket(query.atr_pct) == atr_bucket(entry.atr_pct))
    if query.risk_reward is not None:
        add("rr_bucket", rr_bucket(query.risk_reward) == rr_bucket(entry.risk_reward))
    if query.sl_margin_loss_pct is not None:
        add("risk_bucket", risk_bucket(query.sl_margin_loss_pct) == risk_bucket(
            entry.sl_margin_loss_pct))
    if query.news_sentiment is not None:
        add("sentiment_bucket", sentiment_bucket(query.news_sentiment) == sentiment_bucket(
            entry.news_sentiment))
    if query.fear_greed is not None:
        add("fear_greed_bucket", fear_greed_bucket(query.fear_greed) == fear_greed_bucket(
            entry.fear_greed))
    return earned / total if total else None


@dataclass(frozen=True)
class SimilarResult:
    cohort: list[SimilarCase]  # todos los que superan el puntaje minimo
    winners: list[SimilarCase]
    losers: list[SimilarCase]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def find_similar(
    query: QueryFeatures, entries: list[MemoryEntry], *, as_of: datetime, k: int,
    min_score: float = DEFAULT_MIN_SCORE,
) -> SimilarResult:
    """Entradas parecidas a la consulta, cerradas ESTRICTAMENTE antes de `as_of`."""
    as_of = _utc(as_of)
    cohort: list[SimilarCase] = []
    for entry in entries:
        if entry.closed_at >= as_of:
            continue  # sin fuga: lo que aun no habia cerrado no existia para esta decision
        score = similarity(query, entry)
        if score is not None and score >= min_score:
            cohort.append(SimilarCase(score, entry))
    cohort.sort(key=lambda c: (-c.score, -c.entry.closed_at.timestamp(), c.entry.signal_group_key))
    winners = [c for c in cohort if c.entry.outcome == "WIN"][:k]
    losers = [c for c in cohort if c.entry.outcome == "LOSS"][:k]
    return SimilarResult(cohort, winners, losers)


def _case(case: SimilarCase, as_of: datetime) -> dict:
    e = case.entry
    return {
        "similarity": round(case.score, 2), "symbol": e.symbol, "side": e.side,
        "strategies": sorted(e.strategies), "return_pct": round(e.return_pct, 1),
        "close_reason": e.close_reason, "duration_hours": (
            None if e.duration_hours is None else round(e.duration_hours, 1)),
        "days_ago": round((as_of - e.closed_at).total_seconds() / 86400, 1),
    }


async def build_lessons_context(
    db: Database, settings: Settings, *, signal_group_key: str, query: QueryFeatures,
    as_of: datetime, reveal_allowed: bool, record: bool = True,
) -> dict | None:
    """Contexto de lecciones para UNA decision, o `None` si los resultados aun estan ocultos o la
    memoria esta vacia. Es contexto estadistico PASADO: no garantiza nada."""
    if not reveal_allowed:
        return None
    as_of = _utc(as_of)
    entries = await memory_repo.get_entries_closed_before(db, as_of)
    if not entries:
        return None
    result = find_similar(query, entries, as_of=as_of, k=settings.lessons_top_k)
    n = len(result.cohort)
    enough = n >= settings.lessons_min_sample
    context: dict = {
        "evidence": EVIDENCE_OK if enough else EVIDENCE_LOW,
        "n_similar": n,
        "min_sample": settings.lessons_min_sample,
        "note": "estadistica de operaciones pasadas; no garantiza resultados",
    }
    if enough:
        wins = sum(1 for c in result.cohort if c.entry.outcome == "WIN")
        low, high = wilson_interval(wins, n)
        context.update({
            "winrate": round(wins / n, 3), "winrate_ci99": [round(low, 3), round(high, 3)],
            "mean_return_pct": round(sum(c.entry.return_pct for c in result.cohort) / n, 2),
        })
    context["examples_are_anecdotal"] = True
    context["winners"] = [_case(c, as_of) for c in result.winners]
    context["losers"] = [_case(c, as_of) for c in result.losers]

    build_id = await memory_repo.latest_build_id(db)
    applied: list[dict] = []
    ids: list[int] = []
    if build_id is not None:
        patterns = patterns_of_query(query)
        for lesson in await memory_repo.get_lessons(db, build_id, status=ACTIVE):
            if patterns.get(lesson.dimension) == lesson.pattern:
                ids.append(lesson.id or 0)
                applied.append({
                    "dimension": lesson.dimension, "pattern": lesson.pattern, "n": lesson.n,
                    "winrate": round(lesson.winrate or 0.0, 3),
                    "base_winrate": round(lesson.base_winrate or 0.0, 3),
                })
    context["active_lessons"] = applied
    if record:
        await memory_repo.record_application(
            db, signal_group_key=signal_group_key, created_at=datetime.now(UTC),
            build_id=build_id, lesson_ids=ids, n_similar=n, evidence=context["evidence"],
        )
    return context
