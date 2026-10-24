"""Memoria de lecciones (Fase 4), de solo lectura. **Nada con resultados sin la puerta**: las
lecciones llevan tasas de acierto y retornos de operaciones cerradas, y los informes llevan PnL, asi
que `GET /lessons` y `GET /reports` devuelven vacio (y el motivo) salvo que
`app.memory.gate.results_visible` sea verdadero (gate de la medicion abierto Y registro oficial del
veredicto; falla cerrado). `GET /status` solo da CONTEOS y el interruptor. Protegido con
`require_auth` a nivel de router."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_db, get_settings
from app.api.security import require_auth
from app.config import Settings
from app.memory.entries import count_closed_reliable
from app.memory.gate import HIDDEN_RESULTS, results_visible
from app.memory.lessons import ACTIVE
from app.persistence.database import Database
from app.persistence.repositories import memory_repo

router = APIRouter(prefix="/api/memory", dependencies=[Depends(require_auth)])

DISCLAIMER = (
    "Estadistica descriptiva de operaciones pasadas: no garantiza resultados. Se prueban muchas "
    "dimensiones a la vez, asi que una leccion ACTIVE es una pista a vigilar, no una verdad."
)


@router.get("/status")
async def status(
    request: Request, db: Database = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    """`GET /api/memory/status`: interruptor y CONTEOS (nunca tasas ni retornos).

    Args:
        request: la peticion (para saber si el ejecutor quedo armado).
        db: base (inyectada).
        settings: configuracion (inyectada).

    Returns:
        Interruptor, si esta activa, muestra minima, conteos, ultima reconstruccion y si los
        resultados son visibles.

    Efectos:
        Lee `shadow_trades`, `memory_entries`, `lesson_applications` y `lessons`, ademas de
        la puerta de resultados.
    """
    visible = await results_visible(db)
    return {
        "enabled": settings.lessons_memory_enabled,
        "active": getattr(request.app.state, "lessons_runner", None) is not None,
        "min_sample": settings.lessons_min_sample,
        "closed_reliable": await count_closed_reliable(db),
        "entries": await memory_repo.count_entries(db),
        "applications": await memory_repo.count_applications(db),
        "last_build_id": await memory_repo.latest_build_id(db),
        "results_visible": visible,
        "message": None if visible else f"Lecciones, tasas de acierto y retornos {HIDDEN_RESULTS}.",
    }


@router.get("/lessons")
async def lessons(
    status: Literal["ACTIVE", "INSUFFICIENT", "UNSTABLE", "NOT_SIGNIFICANT", "ALL"] = ACTIVE,
    db: Database = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    """`GET /api/memory/lessons`: las lecciones, vacio salvo que los resultados sean visibles.

    Args:
        status: filtro de estado (por defecto `ACTIVE`).
        db: base (inyectada).
        settings: configuracion (inyectada).

    Returns:
        Las lecciones de la ultima reconstruccion, o una lista vacia con el motivo.

    Efectos:
        Lee `lessons` y la puerta de resultados.
    """
    if not await results_visible(db):
        return {
            "results_visible": False, "lessons": [],
            "message": f"Lecciones, tasas de acierto y retornos {HIDDEN_RESULTS}.",
        }
    build_id = await memory_repo.latest_build_id(db)
    rows = await memory_repo.get_lessons(db, build_id, None if status == "ALL" else status)
    return {
        "results_visible": True, "build_id": build_id, "min_sample": settings.lessons_min_sample,
        "disclaimer": DISCLAIMER,
        "lessons": [
            {
                "dimension": x.dimension, "pattern": x.pattern, "n": x.n, "wins": x.wins,
                "winrate": x.winrate, "ci_low": x.ci_low, "ci_high": x.ci_high,
                "base_winrate": x.base_winrate, "mean_return_pct": x.mean_return_pct,
                "half1_delta": x.half1_delta, "half2_delta": x.half2_delta, "status": x.status,
            }
            for x in rows
        ],
    }


@router.get("/reports")
async def reports(
    kind: Literal["POSTMORTEM", "CALIBRATION"], limit: int = Query(default=5, ge=1, le=50),
    db: Database = Depends(get_db),
) -> dict:
    """`GET /api/memory/reports`: informes de post-mortem o calibracion.

    Vacio mientras los resultados esten ocultos.

    Args:
        kind: `POSTMORTEM` o `CALIBRATION`.
        limit: cuantos devolver (1-50).
        db: base (inyectada).

    Returns:
        Los informes, o una lista vacia con el motivo.

    Efectos:
        Lee `lessons_reports` y la puerta de resultados.
    """
    if not await results_visible(db):
        return {
            "results_visible": False, "reports": [],
            "message": f"Los informes llevan PnL y estan {HIDDEN_RESULTS}.",
        }
    return {
        "results_visible": True, "reports": await memory_repo.get_reports(db, kind, limit=limit),
        "disclaimer": DISCLAIMER,
    }
