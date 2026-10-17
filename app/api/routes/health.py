"""`GET /health` (incidente de estabilidad 2026-10-11): expone dos senales
DISTINTAS para detectar un cuelgue rapido --

- `last_beat_at`: el event loop sigue vivo (`app/core/heartbeat.py`, cada
  `settings.heartbeat_seconds`).
- `last_cycle_at`: la ULTIMA evaluacion completa del generador de senales
  (`Scheduler.last_cycle_at`, no el heartbeat -- el loop puede estar vivo y
  el ciclo de negocio seguir atascado, p.ej. con la API de Bitunix caida).

`status` es "stale" si `last_cycle_at` es `None` o tiene mas de
`STALE_AFTER_MINUTES` minutos; ese umbral es una constante del modulo (bien
por encima de `scheduler_poll_seconds`, que por defecto corre cada 60 s).

El bloque `llm_labeling` (TAREA B, docs/FASE3_6_LLM.md seccion k) informa el
etiquetado en vivo de sombras durante la medicion: `frozen_hash_ok=false`
significa que `PROMPT_SHA256` actual no coincide con el congelado y que, por
eso, el etiquetado NO esta corriendo (aunque `enabled` sea `true`) -- ver
`app/trading/shadow_live_labeler.py`.

El bloque `paper_execution` (subfase 3.9) informa el brazo ejecutado de la cuenta paper:
`enabled` es el interruptor, `active` que quedo armado al arrancar, `armed_since` desde
cuando y las aperturas/omisiones del dia. Nunca trae PnL ni resultados."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends

from app.api.deps import get_db, get_heartbeat, get_live_labeler, get_paper_arm, get_scheduler
from app.core.heartbeat import Heartbeat
from app.core.logging import get_dropped_log_count
from app.core.scheduler import Scheduler
from app.llm.budget import local_day_start_utc
from app.llm.decision_service import (
    MEDICION,
    STATUS_BUDGET_EXCEEDED,
    STATUS_ERROR_HTTP,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_TIMEOUT,
)
from app.persistence.database import Database
from app.persistence.repositories import llm_logs_repo
from app.trading import control_state, risk_engine
from app.trading.paper_execution import PaperExecutionArm
from app.trading.shadow_live_labeler import ShadowLiveLabeler

router = APIRouter()

STALE_AFTER_MINUTES = 90

NO_PAPER_EXECUTION = {
    "enabled": False, "active": False, "armed_since": None, "opened_today": 0, "skipped_today": 0,
}


@router.get("/health")
async def health(
    heartbeat: Heartbeat = Depends(get_heartbeat),
    scheduler: Scheduler = Depends(get_scheduler),
    live_labeler: ShadowLiveLabeler = Depends(get_live_labeler),
    db: Database = Depends(get_db),
    paper_arm: PaperExecutionArm | None = Depends(get_paper_arm),
) -> dict:
    last_cycle_at = scheduler.last_cycle_at
    stale = (
        last_cycle_at is None
        or datetime.now(UTC) - last_cycle_at > timedelta(minutes=STALE_AFTER_MINUTES)
    )

    now = datetime.now(UTC)
    since = local_day_start_utc(live_labeler.settings.report_timezone, now)
    counts = await llm_logs_repo.get_status_counts_since(db, since, fase=MEDICION)
    budget_exhausted_at = await llm_logs_repo.get_first_occurrence_since(
        db, since, STATUS_BUDGET_EXCEEDED, fase=MEDICION
    )
    cost_today_usd = await llm_logs_repo.get_spend_since(db, since, fase=MEDICION)

    return {
        "status": "stale" if stale else "ok",
        "mode": "PAPER_TRADING",
        # Subfase 3.7: una pausa del scheduler DETIENE la medicion (y deja el
        # status en "stale"); aqui se ve que fue a proposito.
        "control": {
            "paused": await control_state.is_scheduler_paused(db),
            "kill_switch_active": await risk_engine.is_kill_switch_active(db),
        },
        "last_beat_at": heartbeat.last_beat_at.isoformat() if heartbeat.last_beat_at else None,
        "last_cycle_at": last_cycle_at.isoformat() if last_cycle_at else None,
        "log_dropped_count": get_dropped_log_count(),
        "llm_labeling": {
            "enabled": live_labeler.settings.llm_live_labeling_enabled,
            "frozen_hash_ok": live_labeler.frozen_hash_ok,
            "labeled_today": counts.get(STATUS_OK, 0),
            "failed_today": (
                counts.get(STATUS_TIMEOUT, 0)
                + counts.get(STATUS_ERROR_HTTP, 0)
                + counts.get(STATUS_INVALID, 0)
            ),
            "budget_exceeded_today": counts.get(STATUS_BUDGET_EXCEEDED, 0),
            "budget_exhausted_at": (
                budget_exhausted_at.isoformat() if budget_exhausted_at else None
            ),
            "cost_today_usd": cost_today_usd,
        },
        "paper_execution": (
            await paper_arm.health_block() if paper_arm is not None else NO_PAPER_EXECUTION
        ),
    }
