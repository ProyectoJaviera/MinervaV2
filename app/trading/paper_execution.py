"""Brazo EJECUTADO de la cuenta PAPER (subfase 3.9, docs/FASE3_9_BRAZO_EJECUTADO.md).

Abre en la cuenta paper SOLO las senales que el LLM etiqueto APROBADA y cuya decision llego a
tiempo (`real_open_allowed`: `decision_delay_s <= LLM_REAL_MAX_DELAY_SECONDS`), pasando por el
motor de riesgo y el `PaperBackend` de siempre. **100 % paper**: este modulo solo conoce a
`PaperBackend`; nunca importa `BitunixBackend` (hay un test por AST) ni envia ordenes reales.

**Aislado de la medicion.** Lo invoca `ShadowLiveLabeler` DESPUES de que `decide_group` ya
persistio la etiqueta, y lo hace con `submit`, que crea una TAREA PROPIA y no lanza nunca: un
fallo (o un cuelgue) al abrir no afecta al etiquetado, al scheduler ni al monitor. No escribe
en `shadow_trades`, `llm_logs` ni `trades` por fuera de `PaperBackend.open_position`; su unico
estado propio es `paper_execution_log` (una fila por decision APROBADA considerada, abra o no).

**Reglas, en este orden** (cada una que no abre deja su motivo en `paper_execution_log`):
1. etiqueta distinta de APROBADA -> nada (RECHAZADA y SIN_LLM no son del brazo);
2. reclamo idempotente del grupo (un grupo nunca abre dos veces);
3. decision tardia -> `LATE_DECISION`;
4. scheduler pausado -> `SCHEDULER_PAUSED`;
5. el simbolo ya tiene una posicion abierta -> `POSITION_ALREADY_OPEN`;
6. tamano minimo del contrato (con la qty redondeada hacia abajo a `basePrecision`, como lo
   haria el exchange) -> `NO_CONTRACT_SPEC` / `BELOW_MIN_ORDER`; el margen NO se cambia;
7. `PaperBackend.open_position(..., decision_source="LLM_APROBADA")`: el rechazo del motor de
   riesgo queda como `RISK_<codigo>`, un nivel ya superado como `LEVELS_INVALID` y cualquier
   otro fallo como `OPEN_ERROR` (solo el tipo)."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.execution.paper_backend import InsufficientRiskBudgetError, PaperBackend
from app.execution.pnl import compute_open_fill
from app.llm.budget import local_day_start_utc
from app.llm.decision_service import real_open_allowed
from app.persistence.database import Database
from app.persistence.models import ContractSpec, Side, Trade
from app.persistence.repositories import paper_execution_repo as log_repo
from app.persistence.repositories import specs_repo, system_state_repo, trades_repo
from app.trading import control_state, risk_engine
from app.trading.signal_generator import SignalCandidate, pick_representative_strategy

logger = get_logger(__name__)

# Contrato con el reporte 3.8 (`app/trading/real_money_report.py` define la misma constante y
# las mismas claves: no puede importar este modulo por su guardia de imports; un test las
# compara).
DECISION_SOURCE = "LLM_APROBADA"
KEY_REAL_ARM_STARTED_AT = "real_arm_started_at"
KEY_REAL_ARM_DRAWDOWN_BASELINE = "real_arm_drawdown_baseline"

LABEL_APPROVED = "APROBADA"

REASON_LATE = "LATE_DECISION"
REASON_PAUSED = "SCHEDULER_PAUSED"
REASON_POSITION_OPEN = "POSITION_ALREADY_OPEN"
REASON_NO_SPEC = "NO_CONTRACT_SPEC"
REASON_BELOW_MIN = "BELOW_MIN_ORDER"
REASON_LEVELS = "LEVELS_INVALID"
REASON_OPEN_ERROR = "OPEN_ERROR"
REASON_ABORTED_RESTART = "ABORTED_RESTART"
RISK_PREFIX = "RISK_"


@dataclass(frozen=True)
class OrderSizeCheck:
    """Resultado de comprobar si una orden alcanza el tamano minimo del contrato.

    Attributes:
        qty: cantidad bruta que daria el margen.
        qty_floored: cantidad redondeada hacia abajo a la precision del contrato.
        min_trade_volume: volumen minimo del contrato, o `None` si no se conoce.
        base_precision: decimales de cantidad que admite el contrato, o `None`.
    """
    qty: float
    qty_floored: float
    min_trade_volume: float | None
    base_precision: int | None

    @property
    def executable(self) -> bool:
        """True si la cantidad redondeada llega al minimo del contrato.

        Sin minimo conocido se considera ejecutable (igual que `PaperBackend`).
        """
        if self.min_trade_volume is None:
            return True  # sin minimo conocido: igual que PaperBackend
        return self.qty_floored >= self.min_trade_volume


def floor_to_precision(qty: float, base_precision: int | None) -> float:
    """La cantidad redondeada HACIA ABAJO a `base_precision` decimales.

    La bolsa no admite fracciones del paso. Sin precision conocida devuelve la cantidad igual.

    Args:
        qty: cantidad.
        base_precision: decimales admitidos, o `None`.

    Returns:
        La cantidad redondeada.
    """
    if base_precision is None:
        return qty
    step = 10 ** -base_precision
    return round(math.floor(qty / step + 1e-9) * step, base_precision)


def check_order_size(
    *, margin_usdt: float, leverage: int, price: float, taker_fee_pct: float, spec: ContractSpec,
) -> OrderSizeCheck:
    """Comprueba si una orden alcanza el tamano minimo del contrato, sin tocar el margen.

    Args:
        margin_usdt: margen de la orden, en USDT.
        leverage: apalancamiento.
        price: precio de referencia.
        taker_fee_pct: comision taker como fraccion.
        spec: especificacion del contrato.

    Returns:
        El `OrderSizeCheck`.
    """
    qty = compute_open_fill(margin_usdt, leverage, price, taker_fee_pct).qty
    return OrderSizeCheck(
        qty=qty, qty_floored=floor_to_precision(qty, spec.base_precision),
        min_trade_volume=spec.min_trade_volume, base_precision=spec.base_precision,
    )


def adverse_gap_bps(side: Side, shadow_price: float, executed_price: float) -> float | None:
    """Diferencia entre el precio de entrada ejecutado y el de la sombra, en puntos basicos.

    Es POSITIVA cuando es peor para la posicion (se paga mas en un LONG, se vende mas
    barato en un SHORT).

    Args:
        side: direccion.
        shadow_price: precio de entrada de la sombra.
        executed_price: precio de entrada ejecutado.

    Returns:
        Los puntos basicos, o `None` si el precio de la sombra no es positivo.
    """
    if shadow_price <= 0:
        return None
    raw = (executed_price - shadow_price) / shadow_price * 10_000
    return raw if side == Side.LONG else -raw


class PaperExecutionArm:
    """El brazo EJECUTADO de la cuenta paper: abre solo las senales APROBADA que llegaron a tiempo.

    100 % paper: solo conoce a `PaperBackend`. Lo invoca el etiquetador DESPUES de persistir
    la etiqueta, con `submit`, que crea una tarea propia y no lanza jamas. Su unico estado
    propio es `paper_execution_log` (una fila por decision APROBADA considerada, abra o
    no). Apagado por defecto (`PAPER_EXECUTION_ENABLED`) y se niega a activarse si
    `AUTO_OPEN_WITHOUT_LLM` esta en true.

    Attributes:
        db: conexion a la base.
        settings: configuracion.
        paper_backend: la cuenta paper por la que se abre.
        active: True una vez que `activate()` lo dejo armado.
    """
    def __init__(
        self, db: Database, settings: Settings, paper_backend: PaperBackend,
        *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.settings = settings
        self.paper_backend = paper_backend
        self._clock = clock
        self.active = False
        self._tasks: set[asyncio.Task] = set()

    # --- activacion ------------------------------------------------------------------------

    async def activate(self) -> bool:
        """Arma el brazo (una vez, desde el `lifespan`) si la configuracion lo permite.

        No se activa con `PAPER_EXECUTION_ENABLED` apagado ni con `AUTO_OPEN_WITHOUT_LLM`
        encendido (mezclaria dos fuentes de apertura en la misma cuenta). Resuelve las filas
        PENDING que dejo un reinicio y escribe UNA sola vez `real_arm_started_at` y
        `real_arm_drawdown_baseline` (las claves que espera el reporte de paso a dinero real);
        reiniciar ese criterio es una accion manual y deliberada.

        Returns:
            True si el brazo queda activo.

        Efectos:
            Lee y escribe `paper_execution_log`; lee `trades`; escribe `system_state` (solo si
            las claves `real_arm_*` no existian).
        """
        if not self.settings.paper_execution_enabled:
            logger.info("Brazo ejecutado (paper) desactivado (PAPER_EXECUTION_ENABLED no es true).")
            return False
        if self.settings.auto_open_without_llm:
            logger.critical(
                "Brazo ejecutado (paper) NO activado: AUTO_OPEN_WITHOUT_LLM=true abriria "
                "operaciones SIN_LLM en la misma cuenta y mezclaria las dos fuentes. Dejalo en "
                "false."
            )
            return False
        if not self.settings.llm_live_labeling_enabled:
            logger.warning(
                "PAPER_EXECUTION_ENABLED=true pero LLM_LIVE_LABELING_ENABLED no: el brazo no "
                "recibira ninguna decision y no abrira nada."
            )
        await self._resolve_stale_pending()
        if await system_state_repo.get_state(self.db, KEY_REAL_ARM_STARTED_AT) is None:
            # Escritura UNICA: las claves que espera el reporte 3.8 (dias de paper trading y
            # linea base del contador de drawdown). Reiniciar el criterio es manual y deliberado.
            baseline = await risk_engine.get_drawdown_stop_trigger_count(self.db)
            await system_state_repo.set_state(
                self.db, KEY_REAL_ARM_STARTED_AT, self._clock().isoformat()
            )
            await system_state_repo.set_state(
                self.db, KEY_REAL_ARM_DRAWDOWN_BASELINE, str(baseline)
            )
        self.active = True
        logger.warning(
            "Brazo ejecutado (paper) ACTIVO: abre solo APROBADA con decision <= %.0f s, margen "
            "%.2f USDT x%d, a traves del motor de riesgo; nunca ordenes reales.",
            self.settings.llm_real_max_delay_seconds, self.settings.default_margin_usdt,
            self.settings.leverage,
        )
        return True

    async def _resolve_stale_pending(self) -> None:
        """Una fila PENDING que sobrevive a un reinicio: o el proceso murio antes de abrir
        (-> ABORTED_RESTART) o murio justo despues de crear el trade (-> OPENED)."""
        for entry in await log_repo.get_pending(self.db):
            row = await self.db.fetch_one(
                "SELECT id FROM trades WHERE decision_source = ? AND symbol = ? AND side = ? "
                "AND opened_at >= ? ORDER BY opened_at LIMIT 1",
                (DECISION_SOURCE, entry.symbol, entry.side, entry.created_at.isoformat()),
            )
            if row is not None:
                await log_repo.resolve_opened(
                    self.db, entry.signal_group_key, trade_id=row["id"],
                    details={"resolved_at_startup": True},
                )
            else:
                await log_repo.resolve_skipped(
                    self.db, entry.signal_group_key, reason=REASON_ABORTED_RESTART,
                )

    # --- punto de entrada desde el labeler ------------------------------------------------------

    def submit(self, shadow: Any, result: Any, candidate: SignalCandidate | None) -> None:
        """Encola el tratamiento de una decision en una tarea propia; sincrono y sin excepciones.

        El etiquetador lo llama tras persistir la etiqueta. No hace nada si el brazo no esta
        activo, si no hay candidato o si la etiqueta no es APROBADA.

        Args:
            shadow: la sombra etiquetada.
            result: el resultado de la decision (`label`, `decision_delay_s`).
            candidate: la senal agrupada de la que salio la sombra.

        Efectos:
            Crea una tarea asyncio; ver `handle` para lo que hace.
        """
        try:
            if not self.active or candidate is None:
                return
            if getattr(result, "label", None) != LABEL_APPROVED:
                return
            task = asyncio.create_task(self._run(shadow, result, candidate))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception as exc:  # noqa: BLE001 - el brazo jamas afecta a quien lo llama
            logger.error("Brazo ejecutado (paper): no se pudo encolar (%s).", type(exc).__name__)

    async def _run(self, shadow: Any, result: Any, candidate: SignalCandidate) -> None:
        try:
            await self.handle(shadow, result, candidate)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - nada de aqui sube al labeler ni al scheduler
            logger.error(
                "Brazo ejecutado (paper): fallo inesperado con %s (%s); la medicion y el "
                "etiquetado no se ven afectados.", getattr(shadow, "signal_group_key", "?"),
                type(exc).__name__,
            )
            await self._best_effort_skip(
                getattr(shadow, "signal_group_key", None), REASON_OPEN_ERROR,
                {"error_type": type(exc).__name__},
            )

    async def _best_effort_skip(self, key: str | None, reason: str, details: dict) -> None:
        if key is None:
            return
        try:
            await log_repo.resolve_skipped(self.db, key, reason=reason, details=details)
        except Exception:  # noqa: BLE001, S110 - ya estamos en el camino de error
            pass

    async def shutdown(self) -> None:
        """Cierre ordenado: cancela las tareas pendientes y espera.

        Lo que quede PENDING en `paper_execution_log` se resuelve en el proximo arranque.
        """
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001, S110 - ya se registro en `_run`
                pass
        self._tasks.difference_update(tasks)

    # --- las reglas ------------------------------------------------------------------------------

    async def _skip(self, key: str, reason: str, details: dict | None = None) -> str:
        await log_repo.resolve_skipped(self.db, key, reason=reason, details=details)
        logger.info("Brazo ejecutado (paper): %s no se abre (%s) %s", key, reason, details or "")
        return reason

    async def handle(
        self, shadow: Any, result: Any, candidate: SignalCandidate,
    ) -> str | None:
        """Aplica las reglas del brazo a una decision y deja su rastro en `paper_execution_log`.

        En orden: etiqueta distinta de APROBADA (nada); reclamo idempotente del grupo;
        decision tardia (`LATE_DECISION`, mas de `LLM_REAL_MAX_DELAY_SECONDS`); scheduler
        pausado (`SCHEDULER_PAUSED`); simbolo con posicion abierta (`POSITION_ALREADY_OPEN`);
        sin especificacion de contrato (`NO_CONTRACT_SPEC`); bajo el tamano minimo con la
        cantidad redondeada hacia abajo (`BELOW_MIN_ORDER`, el margen NO se cambia); y por fin
        `PaperBackend.open_position(..., decision_source="LLM_APROBADA")`, cuyo rechazo de
        riesgo queda como `RISK_<codigo>`, un nivel ya superado como `LEVELS_INVALID` y
        cualquier otro fallo como `OPEN_ERROR` (solo el tipo).

        Args:
            shadow: la sombra etiquetada.
            result: el resultado de la decision (`label`, `decision_delay_s`).
            candidate: la senal agrupada de la que salio la sombra.

        Returns:
            `"OPENED"`, el motivo del `SKIPPED`, o `None` si no era del brazo o ya estaba
            reclamado.

        Efectos:
            Lee `system_state`, `trades`, `contract_specs_cache`. Red: GET `tickers` (por
            `open_position`). Escribe `paper_execution_log` y, si abre, `trades`, ademas de lo
            que escribe el motor de riesgo (`risk_rejections`, `system_state`).
        """
        if getattr(result, "label", None) != LABEL_APPROVED:
            return None
        representative = pick_representative_strategy(
            candidate.contributing_strategies, self.settings
        )
        key = shadow.signal_group_key
        claimed = await log_repo.claim(
            self.db, signal_group_key=key, shadow_trade_id=shadow.id, symbol=candidate.symbol,
            side=candidate.side.value, strategy=representative,
            decision_delay_s=result.decision_delay_s, created_at=self._clock(),
        )
        if not claimed:
            return None

        if not real_open_allowed(result.label, result.decision_delay_s, self.settings):
            return await self._skip(key, REASON_LATE, {
                "decision_delay_s": result.decision_delay_s,
                "limit_s": self.settings.llm_real_max_delay_seconds,
            })
        if await control_state.is_scheduler_paused(self.db):
            return await self._skip(key, REASON_PAUSED)
        if await trades_repo.get_open_positions(self.db, candidate.symbol):
            return await self._skip(key, REASON_POSITION_OPEN)

        spec = await specs_repo.get_spec(self.db, candidate.symbol)
        if spec is None:
            return await self._skip(key, REASON_NO_SPEC)
        size = check_order_size(
            margin_usdt=self.settings.default_margin_usdt, leverage=self.settings.leverage,
            price=shadow.entry_price, taker_fee_pct=self.settings.taker_fee_pct, spec=spec,
        )
        if not size.executable:
            return await self._skip(key, REASON_BELOW_MIN, self._size_details(shadow, size))

        try:
            trade = await self.paper_backend.open_position(
                symbol=candidate.symbol, side=candidate.side,
                margin_usdt=self.settings.default_margin_usdt, leverage=self.settings.leverage,
                strategy=representative, sl_margin_loss_pct=candidate.sl_margin_loss_pct,
                decision_source=DECISION_SOURCE,
                levels=candidate.levels_by_strategy.get(representative),
            )
        except risk_engine.RiskRejectedError as exc:
            return await self._skip(key, f"{RISK_PREFIX}{exc.reason}", exc.details)
        except InsufficientRiskBudgetError as exc:
            return await self._skip(key, REASON_BELOW_MIN, {
                **self._size_details(shadow, size), "message": str(exc),
            })
        except ValueError as exc:
            return await self._skip(key, REASON_LEVELS, {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - se registra el tipo y el brazo sigue
            logger.exception("Brazo ejecutado (paper): error abriendo %s", candidate.symbol)
            return await self._skip(key, REASON_OPEN_ERROR, {"error_type": type(exc).__name__})

        await log_repo.resolve_opened(
            self.db, key, trade_id=trade.id, details=self._opened_details(shadow, candidate, trade),
        )
        logger.info(
            "Brazo ejecutado (paper): abierta trade=%d %s %s (decision a %.1f s del cierre)",
            trade.id, trade.symbol, trade.side.value, result.decision_delay_s,
        )
        return "OPENED"

    @staticmethod
    def _size_details(shadow: Any, size: OrderSizeCheck) -> dict:
        return {
            "symbol": shadow.symbol, "price": shadow.entry_price, "qty": size.qty,
            "qty_floored": size.qty_floored, "min_trade_volume": size.min_trade_volume,
            "base_precision": size.base_precision,
        }

    @staticmethod
    def _opened_details(shadow: Any, candidate: SignalCandidate, trade: Trade) -> dict:
        return {
            "entry_delay_s": (trade.opened_at - candidate.candle_close_time).total_seconds(),
            "shadow_entry_price": shadow.entry_price, "executed_entry_price": trade.entry_price,
            "adverse_gap_bps": adverse_gap_bps(trade.side, shadow.entry_price, trade.entry_price),
            "qty": trade.qty, "margin_usdt": trade.margin_usdt,
        }

    # --- visibilidad (sin PnL) -------------------------------------------------------------------

    async def health_block(self) -> dict:
        """El bloque `paper_execution` de `GET /health`: estado del brazo y conteos de hoy, sin PnL.

        Returns:
            Diccionario con `enabled`, `active`, `armed_since`, `opened_today` y `skipped_today`.

        Efectos:
            Lee `paper_execution_log` y `system_state`.
        """
        since = local_day_start_utc(self.settings.report_timezone, self._clock())
        counts = await log_repo.count_outcomes_since(self.db, since)
        return {
            "enabled": self.settings.paper_execution_enabled,
            "active": self.active,
            "armed_since": await system_state_repo.get_state(self.db, KEY_REAL_ARM_STARTED_AT),
            "opened_today": counts.get(log_repo.OUTCOME_OPENED, 0),
            "skipped_today": counts.get(log_repo.OUTCOME_SKIPPED, 0),
        }
