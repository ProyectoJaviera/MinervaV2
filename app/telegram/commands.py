"""Comandos del bot de Telegram (subfase 3.7).

**Reglas que este modulo garantiza por construccion:**
- Nunca muestra PnL ni resultados de las SOMBRAS (`shadow_trades`) ni la comparacion
  APROBADA/RECHAZADA, con el gate abierto o cerrado: el veredicto sale solo de
  `scripts/ai_value_report.py`, despues del placebo (docs/VEREDICTO_3_6.md). De la
  medicion solo lee `app.trading.measurement_status` (conteos y marcas de tiempo, sin
  PnL), y este modulo NO importa `shadow_report`, `ai_value*` ni `shadow_repo`.
- `/positions` y `/balance` reflejan la cuenta REAL SIMULADA (`trades`), nunca las
  sombras.
- Sin llamadas a la red de Bitunix: el PnL flotante usa los marks que ya mantiene el
  `PositionMonitor`.
- Texto plano (sin `parse_mode`), primera linea "MODO PAPER TRADING".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.api.routes.health import STALE_AFTER_MINUTES
from app.config import Settings
from app.core.logging import get_logger
from app.llm.budget import local_day_start_utc
from app.persistence.database import Database
from app.persistence.models import Trade
from app.persistence.repositories import llm_logs_repo, system_state_repo, trades_repo
from app.telegram.confirmation import ConfirmationManager, is_yes
from app.trading import control_state, risk_engine
from app.trading.measurement_status import (
    APPROVAL_BAND,
    MIN_EFFECTIVE_N,
    NON_OK_STATUSES,
    MeasurementStatus,
    compute_status_async,
    is_measurement_gate_open,
    pace_lines,
)

logger = get_logger(__name__)

HEADER = "MODO PAPER TRADING"
COMMANDS = ("/status", "/positions", "/balance", "/metrics", "/pause", "/resume", "/kill")


@dataclass
class BotContext:
    """Dependencias de los comandos. `scheduler`, `live_labeler`, `paper_backend` y
    `monitor` pueden ser objetos con los mismos atributos (en tests, dobles)."""

    db: Database
    settings: Settings
    paper_backend: Any
    scheduler: Any
    live_labeler: Any
    monitor: Any | None = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731

    def marks(self) -> dict[str, float]:
        return dict(getattr(self.monitor, "marks", {}) or {})


def _usd(value: float) -> str:
    return f"{value:+.4f}" if value else "0.0000"


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """('/status', args) o None si no es un comando. Acepta `/status@NombreDelBot`."""
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0].split("@", 1)[0].lower()
    return name, parts[1:]


def help_text() -> str:
    return "\n".join([
        HEADER,
        "Comandos: " + ", ".join(COMMANDS),
        "/pause, /resume y /kill piden confirmacion (repite el comando con SI en 60 s).",
    ])


# --- /status --------------------------------------------------------------------


async def _equity_and_missing(ctx: BotContext) -> tuple[float, float, list[Trade], list[str]]:
    """(balance realizado, equity con los marks disponibles, posiciones, simbolos sin mark)."""
    balance = await ctx.paper_backend.get_balance()
    positions = await trades_repo.get_open_positions(ctx.db)
    marks = ctx.marks()
    unrealized = 0.0
    missing: list[str] = []
    for p in positions:
        if p.symbol in marks:
            unrealized += ctx.paper_backend.unrealized_pnl_usdt(p, marks[p.symbol])
        else:
            missing.append(p.symbol)
    return balance, balance + unrealized, positions, missing


def _measurement_line(status: MeasurementStatus) -> str:
    gate = "ABIERTO" if is_measurement_gate_open(status) else "CERRADO"
    return (
        f"Medicion: APROBADA {status.n_eff_approved}/{MIN_EFFECTIVE_N}, "
        f"RECHAZADA {status.n_eff_rejected}/{MIN_EFFECTIVE_N} | gate {gate}"
    )


async def cmd_status(ctx: BotContext) -> str:
    now = ctx.now()
    balance, equity, positions, missing = await _equity_and_missing(ctx)
    paused = await control_state.is_scheduler_paused(ctx.db)
    kill = await risk_engine.is_kill_switch_active(ctx.db)
    breaker_until = await risk_engine.get_active_circuit_breaker_until(ctx.db)
    drawdown_stop = await risk_engine.is_drawdown_stop_active(ctx.db)
    since = local_day_start_utc(ctx.settings.report_timezone, now)
    spend = await llm_logs_repo.get_spend_since(ctx.db, since)
    status = await compute_status_async(ctx.db)

    last_cycle = ctx.scheduler.last_cycle_at
    if last_cycle is None:
        cycle = "ninguno todavia"
    else:
        age_min = (now - last_cycle).total_seconds() / 60
        stale = " (STALE)" if age_min > STALE_AFTER_MINUTES else ""
        cycle = f"hace {age_min:.0f} min{stale}"
    labeler = ctx.live_labeler
    labeling_on = bool(labeler.settings.llm_live_labeling_enabled)
    lines = [
        HEADER,
        f"Capital inicial {ctx.settings.initial_capital_usdt:.2f} | balance {balance:.2f} | "
        f"equity {equity:.2f} USDT" + (f" (sin mark: {', '.join(missing)})" if missing else ""),
        f"Posiciones abiertas: {len(positions)}/{ctx.settings.max_simultaneous_positions}",
        f"Kill switch: {'ACTIVO' if kill else 'inactivo'} | Scheduler pausado: "
        f"{'SI (la medicion esta detenida)' if paused else 'no'}",
        "Circuit breaker: "
        + (f"ACTIVO hasta {breaker_until.isoformat()}" if breaker_until else "inactivo")
        + " | Stop por drawdown: "
        + ("ACTIVO" if drawdown_stop else "inactivo"),
        f"Gasto LLM hoy: {spend:.4f} / {ctx.settings.llm_daily_budget_usd:.2f} USD",
        f"Ultimo ciclo de senales: {cycle}",
        f"Etiquetado en vivo: {'activo' if labeling_on else 'DESACTIVADO'} | hash congelado OK: "
        f"{'si' if labeler.frozen_hash_ok else 'NO'}",
        _measurement_line(status),
    ]
    return "\n".join(lines)


# --- /positions, /balance -----------------------------------------------------------


async def cmd_positions(ctx: BotContext) -> str:
    positions = await trades_repo.get_open_positions(ctx.db)
    if not positions:
        return f"{HEADER}\nSin posiciones abiertas (cuenta real simulada)."
    marks = ctx.marks()
    lines = [HEADER, f"Posiciones abiertas (cuenta real simulada): {len(positions)}"]
    for p in positions:
        if p.symbol in marks:
            pnl = _usd(ctx.paper_backend.unrealized_pnl_usdt(p, marks[p.symbol]))
            float_txt = f"PnL flotante {pnl} USDT (mark {marks[p.symbol]})"
        else:
            float_txt = "PnL flotante n/d (sin mark todavia)"
        sl = f"SL {p.sl_price}" if p.sl_price is not None else "sin SL"
        tp = f"TP {p.tp_price}" if p.tp_price is not None else "sin TP"
        lines.append(
            f"- {p.symbol} {p.side.value} qty {p.qty} entrada {p.entry_price} | {sl} | {tp} | "
            f"estrategia {p.strategy or 'n/d'} | {float_txt}"
        )
    return "\n".join(lines)


async def cmd_balance(ctx: BotContext) -> str:
    balance, equity, positions, missing = await _equity_and_missing(ctx)
    initial = ctx.settings.initial_capital_usdt
    lines = [
        HEADER,
        "Cuenta real simulada (no incluye sombras):",
        f"- Capital inicial: {initial:.2f} USDT",
        f"- Balance realizado: {balance:.2f} USDT ({_usd(balance - initial)} vs inicial)",
        f"- Equity (balance + PnL flotante): {equity:.2f} USDT"
        + (f" -- parcial, sin mark de {', '.join(missing)}" if missing else ""),
        f"- Posiciones abiertas: {len(positions)}",
    ]
    return "\n".join(lines)


# --- /metrics -------------------------------------------------------------------------


async def _real_account_metrics(ctx: BotContext) -> list[str]:
    """PF y esperanza por estrategia de las operaciones CERRADAS de la cuenta real
    simulada (tabla `trades`), nunca de las sombras."""
    rows = await ctx.db.fetch_all(
        "SELECT strategy, pnl_net_usdt FROM trades WHERE status = 'CLOSED'"
    )
    if not rows:
        return ["Cuenta real simulada: sin operaciones cerradas todavia."]
    by_strategy: dict[str, list[float]] = {}
    for r in rows:
        by_strategy.setdefault(r["strategy"] or "n/d", []).append(r["pnl_net_usdt"] or 0.0)
    lines = [f"Cuenta real simulada: {len(rows)} operaciones cerradas"]
    for strategy in sorted(by_strategy):
        pnls = by_strategy[strategy]
        wins = sum(p for p in pnls if p > 0)
        losses = -sum(p for p in pnls if p < 0)
        pf = "n/d" if losses == 0 else f"{wins / losses:.2f}"
        lines.append(
            f"- {strategy}: {len(pnls)} cerradas | PF {pf} | esperanza "
            f"{_usd(sum(pnls) / len(pnls))} USDT"
        )
    return lines


async def cmd_metrics(ctx: BotContext) -> str:
    """SOLO AVANCE de la medicion (igual que el dashboard) con el gate cerrado Y
    abierto -- decision del 2026-10-17: Telegram nunca muestra la comparacion
    APROBADA/RECHAZADA ni PnL de sombras; el veredicto sale de ai_value_report.py."""
    status = await compute_status_async(ctx.db)
    gate_open = is_measurement_gate_open(status)
    lines = [HEADER, "Avance de la medicion (hash congelado):"]
    if status.total_medicion_rows == 0:
        lines.append("- Sin filas de medicion todavia.")
    else:
        rate = (
            "n/d" if status.approval_rate is None
            else f"{status.approval_rate:.1%} "
            f"({'dentro' if status.within_band else 'FUERA'} de la banda "
            f"{APPROVAL_BAND[0]:.0%}-{APPROVAL_BAND[1]:.0%})"
        )
        lines += [
            f"- N efectivo APROBADA: {status.n_eff_approved}/{MIN_EFFECTIVE_N} "
            f"(faltan {status.shortfall('APROBADA')})",
            f"- N efectivo RECHAZADA: {status.n_eff_rejected}/{MIN_EFFECTIVE_N} "
            f"(faltan {status.shortfall('RECHAZADA')})",
            *(f"- {line}" for line in pace_lines(status)),
            f"- Tasa de aprobacion del LLM: {rate}",
            f"- Filas OK {status.total_ok} de {status.total_medicion_rows}; no OK: "
            + (
                ", ".join(
                    f"{s} {status.non_ok_counts[s]}"
                    for s in NON_OK_STATUSES if status.non_ok_counts[s]
                ) or "ninguna"
            ),
            f"- Sombras omitidas sin llamada al LLM: {status.omitted_without_row}",
            f"- Costo acumulado: {status.total_cost_usd:.4f} USD",
        ]
    if gate_open:
        lines.append(
            "GATE ABIERTO: seguir el procedimiento de docs/VEREDICTO_3_6.md (copia, placebo "
            "oficial y, solo si sale CALIBRADO, scripts/ai_value_report.py). Telegram no "
            "muestra resultados ni la comparacion APROBADA/RECHAZADA."
        )
    else:
        lines.append(
            "Gate CERRADO: no se muestran resultados (PnL) ni la comparacion APROBADA/RECHAZADA "
            "hasta el punto de analisis."
        )
    lines.append("")
    lines += await _real_account_metrics(ctx)
    return "\n".join(lines)


# --- /pause, /resume, /kill (con confirmacion) ------------------------------------------

STOPS_MEASUREMENT = (
    "DETIENE LA MEDICION en curso: sin senales, sin sombras y sin etiquetado del LLM "
    "mientras dure (los cierres de posiciones siguen funcionando)."
)
CONTROL_WARNINGS = {
    "/pause": f"ATENCION: /pause {STOPS_MEASUREMENT}",
    "/kill": (
        "ATENCION: /kill activa el kill switch (bloquea entradas reales nuevas) y ademas "
        f"pausa el scheduler: {STOPS_MEASUREMENT} Se levanta con /resume."
    ),
    "/resume": (
        "/resume levanta la pausa del scheduler Y el kill switch (la medicion se reanuda; "
        "no levanta el stop por drawdown, que se resuelve aparte)."
    ),
}


async def _record_control(ctx: BotContext, command: str) -> None:
    """Rastro auditable de cada accion de control ejecutada (sin datos del chat)."""
    stamp = f"{command} {ctx.now().isoformat()}"
    await system_state_repo.set_state(ctx.db, "telegram_last_control_action", stamp)
    logger.warning("Telegram: accion de control ejecutada: %s", stamp)


async def execute_control(ctx: BotContext, command: str) -> str:
    if command == "/pause":
        await control_state.set_scheduler_paused(ctx.db, True)
        await _record_control(ctx, command)
        return (
            f"{HEADER}\n"
            "Scheduler PAUSADO. La medicion esta DETENIDA (sin senales, sombras ni etiquetado); "
            "los cierres de posiciones siguen. Reanuda con /resume."
        )
    if command == "/kill":
        await risk_engine.activate_kill_switch(ctx.db)
        await control_state.set_scheduler_paused(ctx.db, True)
        await _record_control(ctx, command)
        return (
            f"{HEADER}\n"
            "KILL SWITCH ACTIVO y scheduler PAUSADO. La medicion esta DETENIDA y no se abren "
            "entradas reales; los cierres de posiciones siguen. Levanta todo con /resume."
        )
    await control_state.set_scheduler_paused(ctx.db, False)
    await risk_engine.deactivate_kill_switch(ctx.db)
    await _record_control(ctx, command)
    lines = [HEADER, "Scheduler REANUDADO y kill switch DESACTIVADO: la medicion sigue."]
    if await risk_engine.is_drawdown_stop_active(ctx.db):
        lines.append(
            "OJO: el stop por drawdown sigue ACTIVO; se levanta aparte (resume_drawdown_stop), "
            "no por Telegram."
        )
    return "\n".join(lines)


async def cmd_control(
    ctx: BotContext, command: str, args: list[str], confirmations: ConfirmationManager,
) -> str:
    if not args:
        confirmations.request(command)
        window = int(confirmations.window_seconds)
        return (
            f"{HEADER}\n{CONTROL_WARNINGS[command]}\n"
            f"Para confirmar, envia en {window} s: {command} SI"
        )
    if is_yes(args):
        if confirmations.confirm(command):
            return await execute_control(ctx, command)
        return (
            f"{HEADER}\n"
            f"No hay una solicitud vigente de {command} (nunca la pediste o expiro). Envia "
            f"primero {command} y luego {command} SI. No se hizo ningun cambio."
        )
    return f"{HEADER}\nUso: {command} (y luego {command} SI). No se hizo ningun cambio."


# --- enrutador ---------------------------------------------------------------------------

Handler = Callable[[BotContext], Awaitable[str]]
READ_ONLY: dict[str, Handler] = {
    "/status": cmd_status,
    "/positions": cmd_positions,
    "/balance": cmd_balance,
    "/metrics": cmd_metrics,
}


class CommandRouter:
    """Callable `texto -> respuesta` para `TelegramBot`. Comando desconocido, `/start`
    y `/help`: la lista de comandos. Texto que no es un comando: sin respuesta."""

    def __init__(
        self, ctx: BotContext, confirmations: ConfirmationManager | None = None,
    ) -> None:
        self.ctx = ctx
        self.confirmations = confirmations or ConfirmationManager()

    async def __call__(self, text: str) -> str | None:
        parsed = parse_command(text)
        if parsed is None:
            return None
        name, args = parsed
        # Ningun comando puede quedarse colgado: tope de 20 s (solo lecturas locales
        # y escrituras de milisegundos en `system_state`).
        if name in CONTROL_WARNINGS:
            return await asyncio.wait_for(
                cmd_control(self.ctx, name, args, self.confirmations), timeout=20
            )
        handler = READ_ONLY.get(name)
        if handler is None:
            return help_text()
        return await asyncio.wait_for(handler(self.ctx), timeout=20)
