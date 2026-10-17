"""Etiquetado en vivo de sombras durante la medicion (subfase 3.6, TAREA B --
plan aprobado tras el piloto real de `signal_review_v4`, docs/FASE3_6_LLM.md
seccion k). **Apagado por defecto** (`settings.llm_live_labeling_enabled`):
activarlo es una decision del usuario, nunca de este codigo ni de un deploy.

Reglas del plan aprobado:

- **Fuego y olvido.** `maybe_label` dispara una tarea en segundo plano por
  cada sombra que `Scheduler` abre, sin bloquear ni el ciclo de señales ni
  `PositionMonitor`. Reusa `LlmDecisionService.decide_group` tal cual existe
  (mismo semaforo de concurrencia y el mismo `LlmBudgetTracker` del
  presupuesto diario que ya usa el piloto) con `fase='MEDICION'`.
- **Nunca toca la cuenta real.** Esta clase no importa `PaperBackend` ni
  `trades_repo` de escritura: no abre ni cierra posiciones reales.
  `AUTO_OPEN_WITHOUT_LLM` sigue en `false`, ese camino no se tocó. Desde la subfase
  3.9 puede avisar a un gancho opcional (`on_decision`, el `submit` de
  `app.trading.paper_execution.PaperExecutionArm`) DESPUES de que la etiqueta ya
  quedo persistida; el gancho no devuelve nada, corre en su propia tarea y un fallo
  suyo se registra pero no afecta al etiquetado.
- **Hash congelado, sin descartes silenciosos.** `FROZEN_PROMPT_SHA256` es el
  valor verificado contra el piloto real de v4 (45/45 JSON valido, banda de
  aprobacion OK, signo de funding correcto en los 45 casos -- ver seccion k).
  Si `PROMPT_SHA256` actual (texto + features, `app/llm/prompts.py`, NUNCA
  tocado por este modulo) no coincide, el etiquetado NO corre: los grupos
  quedan `SIN_LLM` sin fila nueva en `llm_logs`. Nunca se descarta una fila
  YA escrita -- solo se deja de escribir filas nuevas mientras el desajuste
  dure, con un WARNING en cada ciclo del scheduler (no una sola vez).
- **Retraso.** Solo se etiqueta una sombra que SIGUE `OPEN` y con
  `decision_delay_s <= MAX_LABELING_DELAY_SECONDS` en el momento en que la
  tarea en segundo plano de verdad corre (no cuando se encola): un reinicio,
  o una cola larga detras del semaforo, puede hacer que pase mas tiempo del
  que parece al disparar la tarea. Si no se cumple, queda `SIN_LLM` sin fila
  nueva -- igual, por diseño, que un grupo de PILOTO nunca llamado.
- **Fallos.** Timeout/HTTP/JSON invalido/presupuesto agotado ya quedan en
  `SIN_LLM`, nunca `RECHAZADA`, dentro de `LlmDecisionService` sin cambios.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.llm.decision_service import MEDICION, LlmDecisionService
from app.llm.prompts import (
    FEATURES_VERSION,
    PROMPT_SHA256,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_features_from_shadow_trade,
    build_user_message,
    estimate_input_tokens,
)
from app.persistence.database import Database
from app.persistence.models import ShadowTrade, TradeStatus
from app.persistence.repositories import shadow_repo

logger = get_logger(__name__)

# Verificado contra el piloto real de `signal_review_v4` (2026-10-12,
# docs/FASE3_6_LLM.md seccion k): 45/45 JSON valido (1 reparado), 60 % APROBAR
# (dentro de banda), 0 nulls, signo del funding correcto en los 45 casos.
# ESTE es el hash que el usuario congelo para empezar la medicion. Un cambio
# de texto o de features en app/llm/prompts.py (prohibido sin su aprobacion
# explicita) cambia PROMPT_SHA256, y este modulo deja de etiquetar en vivo
# hasta que alguien actualice este valor a mano, a proposito.
FROZEN_PROMPT_SHA256 = "0fa0dd75a3fecdae36f3b77c8b7ec5d97d58a2238349283c61bfa7632188c777"

MAX_LABELING_DELAY_SECONDS = 1800.0


class ShadowLiveLabeler:
    def __init__(
        self, db: Database, settings: Settings, decision_service: LlmDecisionService,
        on_decision: Callable[[ShadowTrade, Any, Any], None] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.decision_service = decision_service
        # Gancho sincrono `(sombra, resultado, candidato)`, llamado DESPUES de persistir la
        # etiqueta (subfase 3.9). `None` (por defecto) = comportamiento anterior.
        self.on_decision = on_decision
        self._tasks: set[asyncio.Task] = set()
        self._frozen_hash_ok_this_cycle = True

    @property
    def frozen_hash_ok(self) -> bool:
        """Comparacion en vivo (sin cache): usada por `GET /health`, que debe
        reflejar el estado actual en cada consulta, no el de hace un ciclo."""
        return PROMPT_SHA256 == FROZEN_PROMPT_SHA256

    def note_cycle_start(self) -> None:
        """Llamado UNA vez por ciclo de `Scheduler.run_cycle` (no una vez por
        candidato): deja en cache si el hash coincide para este ciclo, y si
        no, un WARNING -- se repite en cada ciclo mientras el desajuste dure,
        a proposito, para que no pase desapercibido."""
        self._frozen_hash_ok_this_cycle = self.frozen_hash_ok
        if not self._frozen_hash_ok_this_cycle:
            logger.warning(
                "Etiquetado en vivo DETENIDO este ciclo: PROMPT_SHA256 (%s) no "
                "coincide con FROZEN_PROMPT_SHA256 (%s) -- las senales de este "
                "ciclo quedan SIN_LLM, sin fila nueva en llm_logs.",
                PROMPT_SHA256, FROZEN_PROMPT_SHA256,
            )

    def maybe_label(self, trade: ShadowTrade, candidate: Any = None) -> None:
        """Dispara (fuego y olvido) el etiquetado de `trade` si el flag esta
        en `true` Y el hash de este ciclo coincide con el congelado. Nunca
        bloquea al llamador (`Scheduler.run_cycle`). `candidate` (la senal agrupada
        de la que salio la sombra) solo viaja hasta el gancho `on_decision`; el
        etiquetado no lo usa."""
        if not self.settings.llm_live_labeling_enabled:
            return
        if not self._frozen_hash_ok_this_cycle:
            return
        task = asyncio.create_task(self._label_one(trade, candidate))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # Nunca silenciosa: sin esto, una excepcion en una tarea de fuego
            # y olvido solo aparece como "Task exception was never retrieved"
            # en stderr, facil de perder.
            logger.error(
                "Tarea de etiquetado en vivo fallo de forma inesperada", exc_info=exc,
            )

    def _dispatch_decision(self, trade: ShadowTrade, result: Any, candidate: Any) -> None:
        """Avisa al gancho DESPUES de que `decide_group` ya persistio la etiqueta. Nunca
        lanza: lo que haga (o falle) el gancho no cambia el etiquetado."""
        if self.on_decision is None or result is None:
            return
        try:
            self.on_decision(trade, result, candidate)
        except Exception as exc:  # noqa: BLE001 - el etiquetado no depende del gancho
            logger.error(
                "Gancho de decision fallo (%s); el etiquetado no se ve afectado.",
                type(exc).__name__,
            )

    async def _label_one(self, trade: ShadowTrade, candidate: Any = None) -> None:
        current = await shadow_repo.get(self.db, trade.id)
        if current is None:
            return
        now = datetime.now(UTC)
        decision_delay_s = (now - current.candle_close_time).total_seconds()
        if current.status != TradeStatus.OPEN or decision_delay_s > MAX_LABELING_DELAY_SECONDS:
            # Queda SIN_LLM sin fila nueva -- igual, por diseño, que un grupo
            # de PILOTO nunca llamado (ver docstring del modulo).
            return
        features = await build_features_from_shadow_trade(self.db, current, self.settings)
        user_message = build_user_message(features)
        estimated_tokens = estimate_input_tokens(SYSTEM_PROMPT, user_message)
        result = await self.decision_service.decide_group(
            signal_group_key=current.signal_group_key, shadow_trade_id=current.id,
            candle_close_time=current.candle_close_time, system_prompt=SYSTEM_PROMPT,
            prompt_version=PROMPT_VERSION, user_message=user_message,
            estimated_input_tokens=estimated_tokens, features_version=FEATURES_VERSION,
            now=now,
        )
        # `decide_group` ya escribio la etiqueta (y la fila de llm_logs) al volver.
        self._dispatch_decision(current, result, candidate)

    async def shutdown(self) -> None:
        """Cancela toda tarea pendiente (cierre limpio del bot, punto 4 del
        plan) y espera a que terminen de cancelarse antes de volver. Quita
        estas tareas de `self._tasks` de forma explicita en vez de confiar en
        `_on_task_done`: ese callback lo agenda `call_soon` al terminar la
        tarea, y un `await task` ya resuelto no cede el control al loop, asi
        que podria no haber corrido todavia cuando `shutdown` retorna."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - ya se logueo en _on_task_done
                pass
        self._tasks.difference_update(tasks)


def build_decision_service(db: Database, settings: Settings) -> LlmDecisionService:
    """Instancia compartida de `LlmDecisionService` para el etiquetado en
    vivo (`fase='MEDICION'`) -- UNA sola, para que el semaforo(4) y el
    `LlmBudgetTracker` sean los mismos para todas las sombras del proceso."""
    return LlmDecisionService(db, settings, fase=MEDICION)
