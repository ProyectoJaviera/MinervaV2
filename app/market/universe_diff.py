"""Cambios del universo entre refrescos (Fase 5): funcion pura que comparten la API del dashboard
y las notificaciones de Telegram. El universo vigente son los activos `included` del ultimo
snapshot de `asset_universe`; un cambio es que el conjunto de incluidos de un snapshot difiera
del anterior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UniverseChange:
    at: datetime
    added: tuple[str, ...]
    removed: tuple[str, ...]


def diff_sets(
    previous: frozenset[str], current: frozenset[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(entran, salen), ordenados."""
    return tuple(sorted(current - previous)), tuple(sorted(previous - current))


def universe_changes(history: list[tuple[datetime, frozenset[str]]]) -> list[UniverseChange]:
    """Cambios entre snapshots consecutivos. `history` va del mas VIEJO al mas nuevo; el resultado
    va del mas NUEVO al mas viejo. El primer snapshot no es un cambio (no hay anterior)."""
    changes: list[UniverseChange] = []
    for (_, before), (at, after) in zip(history, history[1:], strict=False):
        added, removed = diff_sets(before, after)
        if added or removed:
            changes.append(UniverseChange(at=at, added=added, removed=removed))
    changes.reverse()
    return changes
