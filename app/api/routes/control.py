"""Controles del dashboard (Fase 5): pausar, reanudar y kill switch. Son las UNICAS escrituras de
control de toda la API, y no abren ni cierran posiciones: solo cambian la pausa del scheduler y el
kill switch, con la misma logica que Telegram (`app/trading/control_actions.py`).

**Pausar o activar el kill switch DETIENE LA MEDICION** (sin senales, sombras ni etiquetado del
LLM): por eso cada accion exige confirmacion explicita, muestra la misma advertencia que Telegram y
deja un rastro de quien y cuando (`control_audit`).

Defensas, en este orden (cada una con su prueba):
1. login (router con `require_auth`; cookie httpOnly, SameSite=Strict);
2. el interruptor `DASHBOARD_CONTROLS_ENABLED` (403 si esta apagado);
3. mismo origen: si la peticion trae `Origin` o `Sec-Fetch-Site` de otro sitio, 403;
4. solo `application/json` (415 si no);
5. la palabra de confirmacion exacta de la accion (`PAUSAR`, `REANUDAR`, `KILL`); sin ella, 422 y
   no se cambia nada.
El servidor escucha solo en 127.0.0.1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import get_db, get_settings
from app.api.security import require_auth
from app.config import Settings
from app.core.logging import get_logger
from app.persistence.database import Database
from app.persistence.repositories import control_audit_repo
from app.trading.control_actions import (
    CHANNEL_DASHBOARD,
    CONFIRM_PHRASES,
    DASHBOARD_WARNINGS,
    KILL,
    PAUSE,
    RESUME,
    apply_control,
    read_state,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/control", dependencies=[Depends(require_auth)])

LABELS = {PAUSE: "Pausar medicion", RESUME: "Reanudar", KILL: "Kill switch"}
STOPS_MEASUREMENT_ACTIONS = {PAUSE: True, KILL: True, RESUME: False}
RESULT_MESSAGES = {
    PAUSE: (
        "Scheduler PAUSADO. La medicion esta DETENIDA (sin senales, sombras ni etiquetado); los "
        "cierres de posiciones siguen. Reanuda con Reanudar."
    ),
    KILL: (
        "KILL SWITCH ACTIVO y scheduler PAUSADO. La medicion esta DETENIDA y no se abren "
        "entradas nuevas; los cierres de posiciones siguen. Levanta todo con Reanudar."
    ),
    RESUME: "Scheduler REANUDADO y kill switch DESACTIVADO: la medicion sigue.",
}
DRAWDOWN_NOTE = (
    "OJO: el stop por drawdown sigue ACTIVO; se levanta aparte (resume_drawdown_stop), no desde "
    "el dashboard."
)


class ControlRequest(BaseModel):
    """Cuerpo de `POST /api/control/{action}`: la palabra de confirmacion exacta de la accion."""
    confirm: str = Field(max_length=32)


def require_controls_enabled(settings: Settings = Depends(get_settings)) -> None:
    """Dependencia: rechaza con 403 si `DASHBOARD_CONTROLS_ENABLED` es false.

    Args:
        settings: configuracion (inyectada).

    Raises:
        HTTPException: 403 con los controles apagados.
    """
    if not settings.dashboard_controls_enabled:
        raise HTTPException(
            status_code=403,
            detail="Controles del dashboard desactivados (DASHBOARD_CONTROLS_ENABLED=false).",
        )


def require_same_origin(request: Request) -> None:
    """Rechaza una peticion que un navegador haya originado desde otro sitio.

    El `Origin` debe ser exactamente este servidor (esquema, host y puerto) y `Sec-Fetch-Site`
    debe ser `same-origin` o `none`. `curl` y otras herramientas locales no mandan `Origin` y
    siguen necesitando la cookie de sesion.

    Args:
        request: la peticion.

    Raises:
        HTTPException: 403 si el origen no es el propio servidor.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="Origen no permitido")
    origin = request.headers.get("origin")
    if origin is None:
        return
    own = f"{request.url.scheme}://{request.headers.get('host', '')}".lower()
    parts = urlsplit(origin)
    if f"{parts.scheme}://{parts.netloc}".lower() != own:
        raise HTTPException(status_code=403, detail="Origen no permitido")


def require_json(request: Request) -> None:
    """Dependencia: exige `Content-Type: application/json`.

    Args:
        request: la peticion.

    Raises:
        HTTPException: 415 si el tipo de contenido es otro.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Se espera application/json")


def _state(state) -> dict:
    return {"paused": state.paused, "kill_switch": state.kill_switch}


@router.get("/info")
async def info(
    db: Database = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    """`GET /api/control/info`: advertencias y palabras de confirmacion de cada accion.

    La fuente de verdad es Python; el dashboard solo muestra lo que devuelve esto.

    Args:
        db: base (inyectada).
        settings: configuracion (inyectada).

    Returns:
        Si los controles estan habilitados, el estado actual y los datos de cada accion.

    Efectos:
        Lee `system_state`.
    """
    return {
        "enabled": settings.dashboard_controls_enabled,
        "state": _state(await read_state(db)),
        "actions": {
            action: {
                "label": LABELS[action],
                "confirm_phrase": CONFIRM_PHRASES[action],
                "warning": DASHBOARD_WARNINGS[action],
                "stops_measurement": STOPS_MEASUREMENT_ACTIONS[action],
            }
            for action in (PAUSE, RESUME, KILL)
        },
    }


@router.get("/audit")
async def audit(
    limit: int = Query(default=20, ge=1, le=100), db: Database = Depends(get_db),
) -> dict:
    """`GET /api/control/audit`: las acciones de control mas recientes.

    Args:
        limit: cuantas devolver (1-100).
        db: base (inyectada).

    Returns:
        Diccionario con la lista `entries`.

    Efectos:
        Lee `control_audit`.
    """
    entries = await control_audit_repo.get_recent(db, limit=limit)
    return {
        "entries": [
            {
                "id": e.id,
                "created_at": e.created_at.isoformat(),
                "channel": e.channel,
                "actor": e.actor,
                "action": e.action,
                "paused_before": e.paused_before,
                "kill_before": e.kill_before,
                "paused_after": e.paused_after,
                "kill_after": e.kill_after,
                "changed": e.changed,
            }
            for e in entries
        ],
    }


# Las defensas son DEPENDENCIAS de la ruta (no codigo del cuerpo): FastAPI valida el cuerpo antes de
# ejecutar el endpoint, y asi el interruptor, el origen y el tipo de contenido se comprueban primero
# (tras el login, que es una dependencia del router).
@router.post(
    "/{action}",
    dependencies=[
        Depends(require_controls_enabled), Depends(require_same_origin), Depends(require_json),
    ],
)
async def execute(
    action: Literal["pause", "resume", "kill"],
    body: ControlRequest,
    username: str = Depends(require_auth),
    db: Database = Depends(get_db),
) -> dict:
    """`POST /api/control/{action}`: pausar, reanudar o kill switch, con confirmacion.

    Defensas en orden (todas dependencias de la ruta, asi se comprueban antes del cuerpo):
    login, interruptor `DASHBOARD_CONTROLS_ENABLED`, mismo origen, solo JSON y la palabra de
    confirmacion exacta (`PAUSAR`, `REANUDAR` o `KILL`). Pausar o activar el kill switch
    DETIENE LA MEDICION. No abre ni cierra posiciones.

    Args:
        action: `pause`, `resume` o `kill`.
        body: la palabra de confirmacion.
        username: usuario autenticado (inyectado).
        db: base (inyectada).

    Returns:
        Resultado con el estado antes y despues, el actor, el id de auditoria y un mensaje.

    Raises:
        HTTPException: 422 si la palabra de confirmacion no coincide (no se cambia nada).

    Efectos:
        Escribe `system_state` (pausa y kill switch) y `control_audit`.
    """
    if body.confirm != CONFIRM_PHRASES[action]:
        raise HTTPException(
            status_code=422,
            detail=f"Para confirmar escribe exactamente {CONFIRM_PHRASES[action]}. "
            "No se hizo ningun cambio.",
        )
    result = await apply_control(
        db, action, channel=CHANNEL_DASHBOARD, actor=username, now=datetime.now(UTC),
    )
    logger.warning(
        "Dashboard: accion de control ejecutada: %s por %s (cambio=%s)", action, username,
        result.changed,
    )
    message = RESULT_MESSAGES[action]
    if not result.changed:
        message = f"Sin cambios (ya estaba asi). {message}"
    if action == RESUME and result.drawdown_stop_active:
        message = f"{message} {DRAWDOWN_NOTE}"
    return {
        "ok": True,
        "action": action,
        "changed": result.changed,
        "before": _state(result.before),
        "after": _state(result.after),
        "actor": username,
        "audit_id": result.audit_id,
        "at": datetime.now(UTC).isoformat(),
        "drawdown_stop_active": result.drawdown_stop_active,
        "message": message,
    }
