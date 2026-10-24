"""Tarea aislada que mantiene la memoria de lecciones (Fase 4): cada `LESSONS_REBUILD_HOURS` agrega
las operaciones cerradas nuevas, reconstruye las lecciones, guarda un post-mortem si hay una racha
perdedora y la tabla de calibracion. **Apagada por defecto** (`LESSONS_MEMORY_ENABLED`) y NO se
activa en ningun puerto durante la medicion. Mismas garantias que el colector de noticias
(`app/news/runtime.py`): construirla no lanza, tarea propia, nada de lo que le pase llega al resto.

LEE resultados (PnL) para aprender, pero solo ESCRIBE en las tablas de memoria; ningun resultado se
muestra sin `is_outcome_reveal_allowed`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.config import Settings
from app.core.logging import get_logger
from app.memory.calibration import save_calibration
from app.memory.entries import refresh_entries
from app.memory.lessons import ACTIVE, rebuild_lessons
from app.memory.postmortem import maybe_postmortem
from app.persistence.database import Database

logger = get_logger(__name__)


class LessonsRunner:
    """Tarea aislada que mantiene la memoria de lecciones, cada `LESSONS_REBUILD_HOURS`.

    Agrega las operaciones cerradas nuevas, reconstruye las lecciones, guarda un post-mortem
    si hay una racha perdedora y la tabla de calibracion. LEE resultados para aprender, pero
    solo ESCRIBE en las tablas de memoria. Apagada por defecto y NO se activa en ningun
    puerto durante la medicion.

    Attributes:
        last_run_at: hora de la ultima pasada completa, o `None`.
    """
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._stop = asyncio.Event()
        self.last_run_at: datetime | None = None

    def stop(self) -> None:
        """Pide que `run_forever` termine despues de su espera actual."""
        self._stop.set()

    async def run_once(self, now: datetime | None = None) -> dict:
        """Una pasada completa: entradas, lecciones, post-mortem y calibracion.

        Args:
            now: hora de la pasada; por defecto, ahora (UTC).

        Returns:
            Resumen con las entradas agregadas, el `build_id`, las lecciones y las activas.

        Efectos:
            Lee `shadow_trades`, `trades`, `llm_logs`, `news_items`; escribe `memory_entries`,
            `lessons` y `lessons_reports`.
        """
        now = now or datetime.now(UTC)
        added = await refresh_entries(self.db, now=now)
        build_id, lessons = await rebuild_lessons(
            self.db, min_sample=self.settings.lessons_min_sample, now=now
        )
        postmortem = await maybe_postmortem(
            self.db, n_losses=self.settings.consecutive_losses_circuit_breaker, now=now
        )
        calibration = await save_calibration(self.db, now=now)
        self.last_run_at = now
        return {
            "entries_added": added, "build_id": build_id, "lessons": len(lessons),
            "active": sum(1 for x in lessons if x.status == ACTIVE),
            "postmortem": postmortem, "calibration": calibration,
        }

    async def run_forever(self, startup_delay_s: float = 300.0) -> None:
        """Espera el arranque y repite `run_once` cada `LESSONS_REBUILD_HOURS` hasta `stop()`.

        Args:
            startup_delay_s: espera inicial, para no competir con el arranque del bot.

        Efectos:
            Ver `run_once`. Un error inesperado se registra y el bucle sigue.
        """
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=startup_delay_s)
            return
        except TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                summary = await self.run_once()
                logger.info("Memoria de lecciones actualizada: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - el bucle sobrevive a cualquier fallo
                logger.exception("Error inesperado en la memoria de lecciones")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.lessons_rebuild_hours * 3600
                )
            except TimeoutError:
                pass


def build_lessons_runner(settings: Settings, db: Database) -> LessonsRunner | None:
    """El ejecutor de la memoria listo para correr, o `None` (desactivado), sin lanzar nunca.

    Args:
        settings: configuracion (`LESSONS_MEMORY_ENABLED`).
        db: conexion a la base.

    Returns:
        El `LessonsRunner`, o `None`.
    """
    try:
        if not settings.lessons_memory_enabled:
            logger.info("Memoria de lecciones desactivada (LESSONS_MEMORY_ENABLED no es true).")
            return None
        return LessonsRunner(db, settings)
    except Exception as exc:  # noqa: BLE001 - construirlo jamas impide arrancar el sistema
        logger.error("Memoria de lecciones NO iniciada (%s); el resto del sistema sigue.",
                     type(exc).__name__)
        return None


async def run_isolated(runner: LessonsRunner) -> None:
    """Corre la memoria capturando cualquier `Exception` que escape.

    Args:
        runner: el ejecutor.
    """
    try:
        await runner.run_forever()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - un bug no se propaga a nadie
        logger.error("La memoria de lecciones termino de forma inesperada (%s); el resto del "
                     "sistema sigue funcionando.", type(exc).__name__)


def maybe_start_lessons_runner(
    settings: Settings, db: Database, tasks: list[asyncio.Task],
) -> LessonsRunner | None:
    """Si corresponde, crea la tarea de la memoria y la agrega a `tasks`.

    Asi el cierre ordenado del `lifespan` la cancela igual que a las demas. Nunca lanza.

    Args:
        settings: configuracion (`LESSONS_MEMORY_ENABLED`).
        db: conexion a la base.
        tasks: lista de tareas del `lifespan`.

    Returns:
        El ejecutor, o `None` si esta desactivada.

    Efectos:
        Crea una tarea asyncio y agrega una entrada al log.
    """
    runner = build_lessons_runner(settings, db)
    if runner is None:
        return None
    try:
        tasks.append(asyncio.create_task(run_isolated(runner)))
    except Exception as exc:  # noqa: BLE001
        logger.error("Memoria de lecciones NO iniciada (%s); el resto del sistema sigue.",
                     type(exc).__name__)
        return None
    logger.warning(
        "Memoria de lecciones ACTIVA (lee resultados de operaciones cerradas; no los muestra "
        "sin el registro oficial del veredicto)."
    )
    return runner
