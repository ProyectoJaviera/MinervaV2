"""Acciones de control compartidas por Telegram y el dashboard (Fase 5, docs/FASE5_PLAN.md): pausar,
reanudar y kill switch, con la MISMA semantica en los dos canales y un rastro auditable de quien y
cuando (`control_audit`).

- **pausar**: el scheduler queda pausado.
- **kill**: activa el kill switch (bloquea entradas nuevas de la cuenta paper) y ademas pausa el
  scheduler.
- **reanudar**: levanta la pausa Y el kill switch. No levanta el stop por drawdown, que se resuelve
  aparte (`risk_engine.resume_drawdown_stop`).

**Pausar el scheduler DETIENE LA MEDICION** (sin senales, sin sombras, sin etiquetado del LLM):
por eso el texto de advertencia (`STOPS_MEASUREMENT`) es una sola constante que muestran los dos
canales. Los cierres de posiciones (SL/TP/trailing/liquidacion) no dependen del scheduler y siguen.

Este modulo solo escribe `system_state` (via `control_state` y `risk_engine`) y `control_audit`: no
abre ni cierra posiciones, no toca sombras, etiquetas ni el prompt."""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.repositories import control_audit_repo
from app.trading import control_state, risk_engine

PAUSE = "pause"
RESUME = "resume"
KILL = "kill"
ACTIONS = (PAUSE, RESUME, KILL)

CHANNEL_DASHBOARD = control_audit_repo.CHANNEL_DASHBOARD
CHANNEL_TELEGRAM = control_audit_repo.CHANNEL_TELEGRAM

# Palabra que hay que escribir para confirmar cada accion en el dashboard (el servidor la exige
# igual que el dialogo: una peticion sin ella se rechaza).
CONFIRM_PHRASES = {PAUSE: "PAUSAR", RESUME: "REANUDAR", KILL: "KILL"}

STOPS_MEASUREMENT = (
    "DETIENE LA MEDICION en curso: sin senales, sin sombras y sin etiquetado del LLM "
    "mientras dure (los cierres de posiciones siguen funcionando)."
)
RESUME_LIFTS = (
    "levanta la pausa del scheduler Y el kill switch (la medicion se reanuda; "
    "no levanta el stop por drawdown, que se resuelve aparte)."
)

# Advertencias del dashboard: el mismo nucleo que las de Telegram (`app/telegram/commands.py`),
# nombrando los botones en vez de los comandos.
DASHBOARD_WARNINGS = {
    PAUSE: f"ATENCION: pausar {STOPS_MEASUREMENT}",
    KILL: (
        "ATENCION: el kill switch bloquea entradas nuevas y ademas pausa el scheduler: "
        f"{STOPS_MEASUREMENT} Se levanta con Reanudar."
    ),
    RESUME: f"Reanudar {RESUME_LIFTS}",
}

_locks: weakref.WeakKeyDictionary[Database, asyncio.Lock] = weakref.WeakKeyDictionary()


def _lock_for(db: Database) -> asyncio.Lock:
    """Un candado por base: dos acciones simultaneas (doble clic, dashboard y Telegram a la vez)
    se aplican una despues de otra y su rastro `antes/despues` queda coherente."""
    lock = _locks.get(db)
    if lock is None:
        lock = _locks[db] = asyncio.Lock()
    return lock


@dataclass(frozen=True)
class ControlState:
    """Estado de los controles: scheduler pausado y kill switch activo."""
    paused: bool
    kill_switch: bool


@dataclass(frozen=True)
class ControlResult:
    """Resultado de aplicar una accion de control.

    Attributes:
        action: `pause`, `resume` o `kill`.
        before: estado antes de la accion.
        after: estado despues.
        audit_id: id de la fila de `control_audit`.
        drawdown_stop_active: si el stop por drawdown sigue activo (la accion no lo levanta).
    """
    action: str
    before: ControlState
    after: ControlState
    audit_id: int
    drawdown_stop_active: bool

    @property
    def changed(self) -> bool:
        """True si la accion cambio algo (pausar un scheduler ya pausado no cambia nada)."""
        return self.before != self.after


async def read_state(db: Database) -> ControlState:
    """El estado actual de los controles.

    Args:
        db: conexion a la base.

    Returns:
        Un `ControlState` con la pausa y el kill switch.

    Efectos:
        Lee `system_state`.
    """
    return ControlState(
        paused=await control_state.is_scheduler_paused(db),
        kill_switch=await risk_engine.is_kill_switch_active(db),
    )


async def apply_control(
    db: Database, action: str, *, channel: str, actor: str, now: datetime | None = None,
) -> ControlResult:
    """Aplica una accion de control y deja su rastro; igual en Telegram y en el dashboard.

    `pause` pausa el scheduler; `kill` activa el kill switch Y pausa el scheduler;
    `resume` levanta la pausa Y el kill switch (no el stop por drawdown, que se resuelve
    aparte). Un candado por base serializa acciones simultaneas para que el rastro
    antes/despues sea coherente.

    Args:
        db: conexion a la base.
        action: `pause`, `resume` o `kill`.
        channel: `DASHBOARD` o `TELEGRAM`.
        actor: usuario autenticado, o `telegram` (sin datos del chat); se recorta a 64.
        now: hora de la accion; por defecto, ahora (UTC).

    Returns:
        El `ControlResult`.

    Raises:
        ValueError: si la accion o el canal son desconocidos.

    Efectos:
        Escribe `system_state` (pausa y kill switch) y `control_audit` (INSERT).
    """
    if action not in ACTIONS:
        raise ValueError(f"accion de control desconocida: {action!r}")
    if channel not in (CHANNEL_DASHBOARD, CHANNEL_TELEGRAM):
        raise ValueError(f"canal de control desconocido: {channel!r}")
    actor = (actor or "").strip()[:64] or "desconocido"
    async with _lock_for(db):
        before = await read_state(db)
        if action == PAUSE:
            await control_state.set_scheduler_paused(db, True)
        elif action == KILL:
            await risk_engine.activate_kill_switch(db)
            await control_state.set_scheduler_paused(db, True)
        else:
            await control_state.set_scheduler_paused(db, False)
            await risk_engine.deactivate_kill_switch(db)
        after = await read_state(db)
        audit_id = await control_audit_repo.insert(
            db, channel=channel, actor=actor, action=action, paused_before=before.paused,
            kill_before=before.kill_switch, paused_after=after.paused,
            kill_after=after.kill_switch, created_at=now or datetime.now(UTC),
        )
        return ControlResult(
            action=action, before=before, after=after, audit_id=audit_id,
            drawdown_stop_active=await risk_engine.is_drawdown_stop_active(db),
        )
