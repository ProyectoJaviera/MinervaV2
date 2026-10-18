"""Puerta de los resultados: todo lo que lleve PnL, retornos o tasas de acierto de la memoria pasa
por `results_visible`, que es `is_outcome_reveal_allowed` (gate de la medicion abierto Y registro
oficial del veredicto). Sin eso solo se puede decir CUANTAS operaciones cerradas hay."""

from __future__ import annotations

from app.core.logging import get_logger
from app.persistence.database import Database
from app.trading.measurement_status import compute_status_async, is_outcome_reveal_allowed

logger = get_logger(__name__)

HIDDEN_RESULTS = "ocultos hasta el registro oficial del veredicto (regla del 2026-10-17)"


async def results_visible(db: Database) -> bool:
    """Falla CERRADO: ante cualquier error al evaluar la puerta, los resultados siguen ocultos."""
    try:
        return is_outcome_reveal_allowed(await compute_status_async(db))
    except Exception as exc:  # noqa: BLE001 - ante la duda, no se muestra nada
        logger.error(
            "No se pudo evaluar la puerta de resultados (%s); se mantienen ocultos.",
            type(exc).__name__,
        )
        return False
