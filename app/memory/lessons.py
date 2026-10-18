"""Lecciones agregadas: patrones de operaciones cerradas con su tasa de acierto y retorno, y la
regla que decide si una leccion puede INFLUIR (evitar el sobreajuste, SPEC "Memoria y
aprendizaje").

Una leccion solo es `ACTIVE` si cumple las TRES condiciones:
1. **muestra minima** (`LESSONS_MIN_SAMPLE`, 30 por defecto) -- si no, `INSUFFICIENT`;
2. el intervalo de Wilson de su tasa de acierto **excluye la tasa base** -- si no,
   `NOT_SIGNIFICANT`. Se usa el 99 % (z = 2,576) y no el 95 %: se prueban muchas dimensiones a la
   vez y el 95 % dejaria pasar falsos positivos por pura multiplicidad;
3. el **signo se mantiene en las dos mitades del tiempo** (la tasa del patron menos la tasa base
   de esa mitad tiene el mismo signo en ambas) -- si no, `UNSTABLE`.

Esto NO es una garantia: es un filtro contra patrones que son ruido. Las lecciones llevan tasas de
acierto: se calculan siempre, pero NUNCA se muestran sin `is_outcome_reveal_allowed`."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime

from app.memory.features import DIMENSIONS, UNKNOWN, patterns_of
from app.persistence.database import Database
from app.persistence.repositories import memory_repo
from app.persistence.repositories.memory_repo import LessonRow, MemoryEntry

Z_99 = 2.576
MIN_HALF_SAMPLE = 5

ACTIVE = "ACTIVE"
INSUFFICIENT = "INSUFFICIENT"
UNSTABLE = "UNSTABLE"
NOT_SIGNIFICANT = "NOT_SIGNIFICANT"


def wilson_interval(wins: int, n: int, z: float = Z_99) -> tuple[float, float]:
    """Intervalo de Wilson de una proporcion. (0, 1) si `n` es 0."""
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _rate(entries: list[MemoryEntry]) -> float | None:
    return sum(1 for e in entries if e.outcome == "WIN") / len(entries) if entries else None


def _delta(group: list[MemoryEntry], half: list[MemoryEntry]) -> float | None:
    """Tasa del patron en esta mitad menos la tasa base de la mitad; `None` sin muestra."""
    base, rate = _rate(half), _rate(group)
    if base is None or rate is None or len(group) < MIN_HALF_SAMPLE:
        return None
    return rate - base


def build_lessons(entries: list[MemoryEntry], *, min_sample: int) -> list[LessonRow]:
    """Todas las lecciones (cualquier estado) de una lista de entradas. Puro y determinista."""
    if not entries:
        return []
    ordered = sorted(entries, key=lambda e: (e.closed_at, e.signal_group_key))
    middle = len(ordered) // 2
    first, second = ordered[:middle], ordered[middle:]
    first_keys = {e.signal_group_key for e in first}
    base = _rate(ordered)
    grouped: dict[tuple[str, str], list[MemoryEntry]] = defaultdict(list)
    for entry in ordered:
        for dimension, pattern in patterns_of(entry).items():
            if pattern != UNKNOWN:
                grouped[(dimension, pattern)].append(entry)

    lessons: list[LessonRow] = []
    for dimension in DIMENSIONS:
        for (dim, pattern), group in sorted(grouped.items()):
            if dim != dimension:
                continue
            n, wins = len(group), sum(1 for e in group if e.outcome == "WIN")
            low, high = wilson_interval(wins, n)
            half1 = [e for e in group if e.signal_group_key in first_keys]
            half2 = [e for e in group if e.signal_group_key not in first_keys]
            d1, d2 = _delta(half1, first), _delta(half2, second)
            if n < min_sample:
                status = INSUFFICIENT
            elif base is not None and low <= base <= high:
                status = NOT_SIGNIFICANT
            elif d1 is None or d2 is None or (d1 > 0) != (d2 > 0) or d1 == 0 or d2 == 0:
                status = UNSTABLE
            else:
                status = ACTIVE
            lessons.append(LessonRow(
                dimension=dim, pattern=pattern, n=n, wins=wins, winrate=wins / n, ci_low=low,
                ci_high=high, base_winrate=base,
                mean_return_pct=sum(e.return_pct for e in group) / n, half1_delta=d1,
                half2_delta=d2, status=status,
            ))
    return lessons


async def rebuild_lessons(
    db: Database, *, min_sample: int, now: datetime | None = None,
) -> tuple[str, list[LessonRow]]:
    """Reconstruye las lecciones de TODA la memoria y las guarda bajo un `build_id` nuevo."""
    now = now or datetime.now(UTC)
    entries = await memory_repo.get_entries_closed_before(db)
    lessons = build_lessons(entries, min_sample=min_sample)
    build_id = f"{now:%Y%m%dT%H%M%S}-n{len(entries)}"
    if lessons:
        await memory_repo.replace_lessons(db, build_id, now, lessons)
    return build_id, lessons
