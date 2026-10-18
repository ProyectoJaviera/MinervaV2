"""Puerta de los resultados de la cuenta paper para la API, el dashboard y las notificaciones
(Fase 5, docs/FASE5_PLAN.md). Es `is_outcome_reveal_allowed` (gate de la medicion abierto Y registro
oficial del veredicto) pero **falla CERRADO**: ante cualquier error al evaluarla, los resultados
(PnL, precio de salida, motivo de cierre, saldo, equity) siguen ocultos. Mientras la puerta este
cerrada solo se puede mostrar estructura (simbolo, lado, niveles, horas) y conteos."""

from __future__ import annotations

from app.core.logging import get_logger
from app.persistence.database import Database
from app.trading.measurement_status import compute_status_async, is_outcome_reveal_allowed

logger = get_logger(__name__)

HIDDEN_RESULTS = "ocultos hasta el registro oficial del veredicto (regla del 2026-10-17)"


async def reveal_allowed(db: Database) -> bool:
    try:
        return is_outcome_reveal_allowed(await compute_status_async(db))
    except Exception as exc:  # noqa: BLE001 - ante la duda, no se muestra ningun resultado
        logger.error(
            "No se pudo evaluar la puerta de resultados (%s); se mantienen ocultos.",
            type(exc).__name__,
        )
        return False
