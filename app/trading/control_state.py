"""Pausa persistente del scheduler (subfase 3.7, bot de Telegram).

`scheduler_paused` vive en `system_state` (igual que el kill switch y el resto del
estado de riesgo): sobrevive a un reinicio del bot, a proposito -- una pausa pedida
por Telegram no debe "olvidarse" sola.

**Pausar el scheduler DETIENE LA MEDICION**: `Scheduler.run_cycle` es lo que genera
senales, abre sombras y dispara el etiquetado en vivo del LLM. NO detiene el monitor
de posiciones ni los cierres (SL/TP/trailing/liquidacion/manual), que son
independientes del scheduler.

El kill switch (`risk_engine.activate_kill_switch`) es OTRA cosa: solo bloquea
entradas REALES nuevas y no detiene senales ni sombras."""

from __future__ import annotations

from app.persistence.database import Database
from app.persistence.repositories import system_state_repo

KEY_SCHEDULER_PAUSED = "scheduler_paused"


async def is_scheduler_paused(db: Database) -> bool:
    return await system_state_repo.get_state(db, KEY_SCHEDULER_PAUSED, "0") == "1"


async def set_scheduler_paused(db: Database, paused: bool) -> None:
    await system_state_repo.set_state(db, KEY_SCHEDULER_PAUSED, "1" if paused else "0")
