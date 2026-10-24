"""Notificaciones push de Telegram (Fase 5, docs/FASE5_PLAN.md): avisa al chat permitido cuando
se abre o se cierra una posicion de la cuenta paper, cuando cambia el universo y cuando cambia el
estado de riesgo (kill switch, pausa, circuit breaker, stop por drawdown, perdida diaria).

**Sin ganchos en el nucleo.** El notificador SONDEA la base de solo lectura cada
`TELEGRAM_PUSH_POLL_SECONDS` y compara con el ultimo estado notificado. Nada de `PaperBackend`,
el monitor, el brazo ejecutado, el scheduler ni el motor de riesgo sabe que existe: una falla
suya no puede alcanzarlos. Lo unico que escribe es la clave `push_state` de `system_state`.

- **Estado persistido** (`push_state`, un JSON): al primer arranque toma el estado actual como
  linea base SIN notificar el historial (no inunda el chat). Despues avanza evento por evento,
  solo tras un envio correcto o un error permanente: nada se repite ni se pierde por un fallo a
  medias.
- **Posiciones**: por diferencia de estado (nuevas por id, y abiertas que ahora estan cerradas);
  asi un cierre retroactivo de la reconciliacion tambien se avisa.
- **Resultados ocultos**: el cierre dice solo "posicion cerrada" (simbolo, lado, estrategia y
  horas) mientras la puerta de resultados (`app.trading.reveal`, falla cerrada) este cerrada. Las
  alertas de riesgo no llevan cifras.
- **Envio**: texto plano; >= 1,2 s entre mensajes (la Bot API pide no pasar de ~1 por segundo
  por chat), `retry_after` respetado ante un 429, reintentos con espera creciente para fallos
  transitorios y descarte registrado (solo el tipo, nunca el token) para los permanentes
  (400/401/403/404); como maximo 10 mensajes por sondeo.
- **Aislado**: tarea propia, construirlo no lanza y todo error se captura dentro."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.core.logging import get_logger
from app.market.attribution import COINGECKO_TEXT, COINGECKO_URL
from app.market.universe_diff import diff_sets
from app.persistence.database import Database
from app.persistence.models import Trade, TradeStatus
from app.persistence.repositories import (
    control_audit_repo,
    system_state_repo,
    trades_repo,
    universe_repo,
)
from app.telegram.client import TelegramApiError
from app.trading import control_state, risk_engine
from app.trading.reveal import HIDDEN_RESULTS, reveal_allowed

logger = get_logger(__name__)

HEADER = "MODO PAPER TRADING"
STATE_KEY = "push_state"
STATE_VERSION = 1
MAX_PER_POLL = 10
MAX_NEW_TRADES_PER_POLL = 50
MIN_SPACING_S = 1.2
SEND_TIMEOUT_S = 25.0
MAX_ATTEMPTS = 3
RETRY_WAITS_S = (2.0, 5.0, 15.0)
MAX_WAIT_S = 60.0
PERMANENT_STATUS = frozenset({400, 401, 403, 404})
ORIGIN_WINDOW = timedelta(seconds=180)
MIN_POLL_SECONDS = 2.0
MAX_POLL_BACKOFF_S = 300.0
STARTUP_DELAY_S = 15.0

DELIVERED = "DELIVERED"
DROPPED = "DROPPED"
RETRY_LATER = "RETRY_LATER"

RISK_KEYS = ("paused", "kill", "breaker_until", "drawdown_stop", "daily_loss", "drawdown_count")


@dataclass
class PushEvent:
    """`text` es `None` para un evento solo de estado (se aplica sin enviar nada)."""

    kind: str
    text: str | None
    apply: Callable[[dict], None]


# --- formato (texto plano, sin tildes como el resto de los mensajes) -----------------------------


def _num(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.8g}"


def _utc(value: datetime | None) -> str:
    return "n/d" if value is None else value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def format_open(trade: Trade) -> str:
    """El texto de aviso de una posicion abierta (simbolo, lado, niveles, origen).

    Args:
        trade: la posicion.

    Returns:
        El texto plano.
    """
    sl = f"SL {_num(trade.sl_price)}" if trade.sl_price is not None else "sin SL"
    tp = f"TP {_num(trade.tp_price)}" if trade.tp_price is not None else "sin TP"
    liq = (
        f"liquidacion estimada {_num(trade.liq_price)}" if trade.liq_price is not None
        else "sin liquidacion estimada"
    )
    return "\n".join([
        HEADER,
        "Posicion ABIERTA (cuenta paper)",
        f"{trade.symbol} {trade.side.value} | estrategia {trade.strategy or 'n/d'} | "
        f"origen {trade.decision_source or 'n/d'}",
        f"Entrada {_num(trade.entry_price)} | cantidad {_num(trade.qty)} | margen "
        f"{trade.margin_usdt:.2f} USDT x{trade.leverage}",
        f"{sl} | {tp} | {liq}",
        f"Abierta {_utc(trade.opened_at)} (id {trade.id})",
    ])


def format_close(trade: Trade, *, reveal: bool) -> str:
    """El texto de aviso de una posicion cerrada.

    Con la puerta cerrada NO toca ningun campo de resultado (PnL, salida, motivo, costos).

    Args:
        trade: la posicion cerrada.
        reveal: si se pueden mostrar resultados.

    Returns:
        El texto plano.
    """
    lines = [
        HEADER,
        f"Posicion cerrada: {trade.symbol} {trade.side.value} "
        f"(estrategia {trade.strategy or 'n/d'}, id {trade.id})",
        f"Abierta {_utc(trade.opened_at)} | cerrada {_utc(trade.closed_at)}",
    ]
    if not reveal:
        lines.append(f"Resultado, precio de salida y motivo: {HIDDEN_RESULTS}.")
        return "\n".join(lines)
    pnl = trade.pnl_net_usdt
    ret = pnl / trade.margin_usdt * 100 if pnl is not None and trade.margin_usdt else None
    lines.append(
        f"Motivo {trade.close_reason or 'n/d'} | salida {_num(trade.exit_price)} | PnL neto "
        + ("n/d" if pnl is None else f"{pnl:+.4f} USDT")
        + ("" if ret is None else f" ({ret:+.1f} % del margen)")
    )
    return "\n".join(lines)


def format_universe(at: datetime, added: tuple[str, ...], removed: tuple[str, ...],
                    included: frozenset[str]) -> str:
    """El texto de aviso de un cambio del universo, con la atribucion de CoinGecko.

    Args:
        at: instante del refresco.
        added: simbolos que entran.
        removed: simbolos que salen.
        included: universo vigente.

    Returns:
        El texto plano.
    """
    return "\n".join([
        HEADER,
        f"Universo ACTUALIZADO ({_utc(at)})",
        "Entran: " + (", ".join(added) or "ninguno"),
        "Salen: " + (", ".join(removed) or "ninguno"),
        f"Vigente ({len(included)}): " + ", ".join(sorted(included)),
        "Un activo que sale con una posicion abierta no se cierra; solo se bloquean entradas "
        "nuevas.",
        f"{COINGECKO_TEXT} ({COINGECKO_URL})",
    ])


def _message(*lines: str | None) -> str:
    return "\n".join([HEADER, *(line for line in lines if line)])


# --- estado --------------------------------------------------------------------------------------


def _valid_state(state) -> bool:
    try:
        return (
            isinstance(state, dict) and state["version"] == STATE_VERSION
            and isinstance(state["trades"]["max_id"], int)
            and isinstance(state["trades"]["open_ids"], list)
            and isinstance(state["universe"]["included"], list)
            and all(key in state["risk"] for key in RISK_KEYS)
        )
    except (KeyError, TypeError):
        return False


async def risk_snapshot(db: Database) -> dict:
    """La foto del estado de riesgo: pausa, kill, circuit breaker, stop, perdida diaria y contador.

    Args:
        db: conexion a la base.

    Returns:
        Diccionario con las claves de `RISK_KEYS`.

    Efectos:
        Lee `system_state`.
    """
    until = await risk_engine.get_active_circuit_breaker_until(db)
    return {
        "paused": await control_state.is_scheduler_paused(db),
        "kill": await risk_engine.is_kill_switch_active(db),
        "breaker_until": until.isoformat() if until else None,
        "drawdown_stop": await risk_engine.is_drawdown_stop_active(db),
        "daily_loss": await risk_engine.is_daily_loss_limit_locked(db),
        "drawdown_count": await risk_engine.get_drawdown_stop_trigger_count(db),
    }


async def baseline_state(db: Database) -> dict:
    """El estado ACTUAL como linea base: lo que ya paso no se notifica.

    Args:
        db: conexion a la base.

    Returns:
        El diccionario de estado de las notificaciones.

    Efectos:
        Lee `trades`, `asset_universe` y `system_state`.
    """
    row = await db.fetch_one("SELECT COALESCE(MAX(id), 0) AS max_id FROM trades")
    open_rows = await db.fetch_all("SELECT id FROM trades WHERE status = 'OPEN' ORDER BY id")
    history = await universe_repo.get_included_history(db, limit_snapshots=1)
    included = sorted(history[-1][1]) if history else []
    return {
        "version": STATE_VERSION,
        "trades": {"max_id": int(row["max_id"]), "open_ids": [int(r["id"]) for r in open_rows]},
        "universe": {"included": included},
        "risk": await risk_snapshot(db),
    }


def _apply_open(trade_id: int) -> Callable[[dict], None]:
    def apply(state: dict) -> None:
        trades = state["trades"]
        trades["max_id"] = max(trades["max_id"], trade_id)
        if trade_id not in trades["open_ids"]:
            trades["open_ids"].append(trade_id)
    return apply


def _apply_close(trade_id: int) -> Callable[[dict], None]:
    def apply(state: dict) -> None:
        trades = state["trades"]
        trades["open_ids"] = [i for i in trades["open_ids"] if i != trade_id]
    return apply


def _apply_risk(current: dict, keys: list[str]) -> Callable[[dict], None]:
    def apply(state: dict) -> None:
        for key in keys:
            state["risk"][key] = current[key]
    return apply


def _apply_universe(included: frozenset[str]) -> Callable[[dict], None]:
    def apply(state: dict) -> None:
        state["universe"]["included"] = sorted(included)
    return apply


# --- el notificador ------------------------------------------------------------------------------


class PushNotifier:
    """Notificaciones push: avisa al chat permitido de posiciones, universo y riesgo.

    SONDEA la base de solo lectura cada `TELEGRAM_PUSH_POLL_SECONDS` y compara con el
    ultimo estado notificado; nada del nucleo sabe que existe. Lo unico que escribe es la
    clave `push_state` de `system_state`. Al primer arranque toma la linea base SIN avisar
    el historial; despues avanza evento por evento, solo tras un envio correcto o un error
    permanente. Mientras la puerta de resultados este cerrada, el aviso de cierre no lleva
    cifras.

    Attributes:
        active: True una vez que el estado quedo listo y el bucle corre.
        sent_total: mensajes enviados.
        failed_total: intentos fallidos.
        dropped_total: mensajes descartados por error permanente.
        consecutive_failures: fallos de envio seguidos.
        last_sent_at: hora del ultimo envio, o `None`.
        last_error: tipo del ultimo error (nunca el token), o `None`.
    """
    def __init__(
        self, db: Database, settings: Settings, client, *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        send_timeout_s: float = SEND_TIMEOUT_S,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self.chat_id = settings.telegram_chat_id
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic
        self._send_timeout_s = send_timeout_s
        self._stop = asyncio.Event()
        self._state: dict | None = None
        self._last_send = float("-inf")
        self._failed_polls = 0
        self.active = False
        self.sent_total = 0
        self.failed_total = 0
        self.dropped_total = 0
        self.consecutive_failures = 0
        self.last_sent_at: datetime | None = None
        self.last_error: str | None = None

    # --- ciclo de vida ---------------------------------------------------------------------------

    def stop(self) -> None:
        """Pide que `run_forever` termine despues de su espera actual."""
        self._stop.set()

    async def aclose(self) -> None:
        """Cierra el cliente de Telegram.

        Efectos:
            Cierra las conexiones de red.
        """
        aclose = getattr(self.client, "aclose", None)
        if aclose is not None:
            await aclose()

    @property
    def poll_seconds(self) -> float:
        """Segundos entre sondeos: `TELEGRAM_PUSH_POLL_SECONDS` con un minimo de 2."""
        return max(MIN_POLL_SECONDS, float(self.settings.telegram_push_poll_seconds))

    def health_block(self) -> dict:
        """El bloque `telegram_push` de `GET /health`: solo conteos y el TIPO del ultimo error.

        Returns:
            Diccionario sin el token, el chat ni el texto enviado.
        """
        return {
            "enabled": self.settings.telegram_push_enabled,
            "active": self.active,
            "sent_total": self.sent_total,
            "failed_total": self.failed_total,
            "dropped_total": self.dropped_total,
            "consecutive_failures": self.consecutive_failures,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
            "last_error": self.last_error,
        }

    async def run_forever(self, startup_delay_s: float = STARTUP_DELAY_S) -> None:
        """Espera el arranque, toma la linea base y sondea cada `poll_seconds` hasta `stop()`.

        Ante fallos de sondeo o de envio seguidos la espera crece (tope 5 minutos).

        Args:
            startup_delay_s: espera inicial, para no competir con el arranque del bot.

        Efectos:
            Lee `trades`, `asset_universe` y `system_state`; escribe `system_state`
            (`push_state`). Red: `sendMessage` a Telegram.
        """
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=startup_delay_s)
            return
        except TimeoutError:
            pass
        fresh = False
        while self._state is None and not self._stop.is_set():
            try:
                fresh = await self._ensure_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - se reintenta en el siguiente turno
                logger.error("Notificaciones push: no se pudo leer el estado (%s).",
                             type(exc).__name__)
                await self._wait(self.poll_seconds)
        self.active = True
        logger.warning("Notificaciones push de Telegram ACTIVAS (sondeo cada %.0f s).",
                       self.poll_seconds)
        try:
            await self.send_startup(fresh)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - el mensaje de arranque nunca detiene el bucle
            logger.error("Notificaciones push: fallo el mensaje de arranque (%s).",
                         type(exc).__name__)
        while not self._stop.is_set():
            try:
                await self.poll_once()
                self._failed_polls = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - el bucle sobrevive a cualquier fallo
                self._failed_polls += 1
                logger.error("Notificaciones push: fallo un sondeo (%s).", type(exc).__name__)
            await self._wait(self._next_wait())

    def _next_wait(self) -> float:
        """El periodo normal; con fallos de sondeo o de envio seguidos, espera creciente (tope
        5 min) para no martillar a Telegram ni a la base."""
        failures = self._failed_polls + min(self.consecutive_failures, 4)
        if failures == 0:
            return self.poll_seconds
        return min(self.poll_seconds * 2 ** min(failures, 6), MAX_POLL_BACKOFF_S)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    # --- estado ----------------------------------------------------------------------------------

    async def _ensure_state(self) -> bool:
        """Carga el estado persistido o toma la linea base. `True` si fue una linea base nueva."""
        raw = await system_state_repo.get_state(self.db, STATE_KEY)
        if raw:
            try:
                loaded = json.loads(raw)
            except ValueError:
                loaded = None
            if _valid_state(loaded):
                self._state = loaded
                return False
            logger.warning("Notificaciones push: el estado guardado no es valido; se toma una "
                           "linea base nueva (no se notifica el historial).")
        self._state = await baseline_state(self.db)
        await self._save_state()
        return True

    async def _save_state(self) -> None:
        await system_state_repo.set_state(self.db, STATE_KEY, json.dumps(self._state))

    # --- eventos ---------------------------------------------------------------------------------

    async def _trade_events(self, state: dict) -> list[PushEvent]:
        trades = state["trades"]
        known_open = sorted(set(trades["open_ids"]))
        rows = await self.db.fetch_all(
            "SELECT * FROM trades WHERE id > ? ORDER BY id LIMIT ?",
            (trades["max_id"], MAX_NEW_TRADES_PER_POLL),
        )
        events: list[PushEvent] = []
        reveal: bool | None = None

        async def close_text(trade: Trade) -> str:
            nonlocal reveal
            if reveal is None:
                reveal = await reveal_allowed(self.db)
            return format_close(trade, reveal=reveal)

        for row in rows:
            trade = trades_repo.row_to_trade(row)
            events.append(PushEvent("OPEN", format_open(trade), _apply_open(trade.id)))
            if trade.status == TradeStatus.CLOSED:
                events.append(PushEvent("CLOSE", await close_text(trade), _apply_close(trade.id)))
        for trade_id in known_open:
            trade = await trades_repo.get_trade(self.db, trade_id)
            if trade is None:  # desaparecio: se olvida sin avisar
                events.append(PushEvent("FORGET", None, _apply_close(trade_id)))
            elif trade.status == TradeStatus.CLOSED:
                events.append(PushEvent("CLOSE", await close_text(trade), _apply_close(trade_id)))
        return events

    async def _universe_events(self, state: dict) -> list[PushEvent]:
        history = await universe_repo.get_included_history(self.db, limit_snapshots=1)
        if not history:
            return []
        at, included = history[-1]
        previous = frozenset(state["universe"]["included"])
        if included == previous:
            return []
        added, removed = diff_sets(previous, included)
        return [PushEvent(
            "UNIVERSE", format_universe(at, added, removed, included), _apply_universe(included),
        )]

    async def _origin(self, action: str) -> str | None:
        """Quien y cuando, si hay un registro de control reciente de esa accion."""
        entry = await control_audit_repo.get_latest(self.db)
        if entry is None or entry.action != action:
            return None
        if self._clock() - entry.created_at > ORIGIN_WINDOW:
            return None
        who = "dashboard" if entry.channel == control_audit_repo.CHANNEL_DASHBOARD else "Telegram"
        return f"Origen: {who} ({entry.actor}) a las {entry.created_at:%H:%M} UTC"

    async def _risk_events(self, state: dict) -> list[PushEvent]:
        current = await risk_snapshot(self.db)
        before = state["risk"]
        events: list[PushEvent] = []
        kill_flip = current["kill"] != before["kill"]
        paused_flip = current["paused"] != before["paused"]

        if kill_flip:
            keys = ["kill", "paused"] if paused_flip else ["kill"]
            if current["kill"]:
                text = _message(
                    "ALERTA: KILL SWITCH ACTIVADO. No se abren entradas nuevas.",
                    "La medicion esta DETENIDA (scheduler en pausa)." if current["paused"]
                    else "El scheduler no esta en pausa: la medicion sigue.",
                    await self._origin("kill"),
                )
            else:
                text = _message(
                    "Kill switch DESACTIVADO.",
                    "El scheduler sigue en pausa: la medicion sigue DETENIDA." if current["paused"]
                    else "La medicion sigue.",
                    await self._origin("resume"),
                )
            events.append(PushEvent("KILL_ON" if current["kill"] else "KILL_OFF", text,
                                    _apply_risk(current, keys)))
        elif paused_flip:
            if current["paused"]:
                text = _message(
                    "Scheduler PAUSADO: la medicion esta DETENIDA (sin senales, sombras ni "
                    "etiquetado); los cierres de posiciones siguen.",
                    await self._origin("pause"),
                )
            else:
                text = _message("Scheduler REANUDADO: la medicion sigue.",
                                await self._origin("resume"))
            events.append(PushEvent("PAUSE_ON" if current["paused"] else "PAUSE_OFF", text,
                                    _apply_risk(current, ["paused"])))

        if current["breaker_until"] != before["breaker_until"]:
            if current["breaker_until"]:
                until = datetime.fromisoformat(current["breaker_until"])
                text = _message(
                    f"ALERTA: CIRCUIT BREAKER ACTIVADO hasta {_utc(until)}.",
                    "No se abren entradas nuevas hasta entonces; se levanta solo.",
                )
            else:
                text = _message("Circuit breaker terminado: se vuelven a permitir entradas.")
            events.append(PushEvent("BREAKER_ON" if current["breaker_until"] else "BREAKER_OFF",
                                    text, _apply_risk(current, ["breaker_until"])))

        mode = self.settings.drawdown_stop_mode
        drawdown_flip = current["drawdown_stop"] != before["drawdown_stop"]
        if drawdown_flip:
            if current["drawdown_stop"]:
                text = _message(
                    f"ALERTA: STOP POR DRAWDOWN ACTIVADO (modo {mode}).",
                    "No se abren entradas nuevas hasta reanudarlo a mano (resume_drawdown_stop); "
                    "no se levanta desde Telegram ni desde el dashboard." if mode == "duro"
                    else "Modo alerta: no bloquea entradas, solo avisa.",
                )
            else:
                text = _message("Stop por drawdown LEVANTADO: se vuelven a permitir entradas.")
            events.append(PushEvent(
                "DRAWDOWN_ON" if current["drawdown_stop"] else "DRAWDOWN_OFF", text,
                _apply_risk(current, ["drawdown_stop", "drawdown_count"]),
            ))
        elif current["drawdown_count"] != before["drawdown_count"]:
            if current["drawdown_count"] > before["drawdown_count"] and mode != "duro":
                text = _message(
                    "ALERTA: el drawdown cruzo el umbral de nuevo (modo alerta: no bloquea "
                    "entradas, solo avisa).",
                )
            else:
                text = None  # en modo duro lo cubre la bandera; solo se sincroniza el contador
            events.append(
                PushEvent("DRAWDOWN_CROSS", text, _apply_risk(current, ["drawdown_count"]))
            )

        if current["daily_loss"] != before["daily_loss"]:
            if current["daily_loss"]:
                text = _message(
                    "ALERTA: LIMITE DE PERDIDA DIARIA alcanzado.",
                    "No se abren entradas nuevas hasta el cambio de dia local.",
                )
            else:
                text = _message("Limite de perdida diaria levantado (dia nuevo): se vuelven a "
                                "permitir entradas.")
            events.append(PushEvent("DAILY_LOSS_ON" if current["daily_loss"] else "DAILY_LOSS_OFF",
                                    text, _apply_risk(current, ["daily_loss"])))
        return events

    # --- sondeo y envio --------------------------------------------------------------------------

    async def poll_once(self) -> int:
        """Un sondeo: detecta eventos y los envia en orden (maximo 10 por sondeo).

        La primera vez, sin estado guardado, solo toma la linea base. Cada evento avanza el
        estado y lo guarda solo si se entrego o se descarto; si hay que reintentar, corta.

        Returns:
            Cuantos mensajes se enviaron.

        Efectos:
            Lee `trades`, `asset_universe`, `system_state`, `control_audit`; escribe
            `system_state` (`push_state`). Red: `sendMessage` a Telegram.
        """
        if self._state is None:
            if await self._ensure_state():
                return 0
        state = self._state
        assert state is not None
        events = [
            *await self._trade_events(state),
            *await self._universe_events(state),
            *await self._risk_events(state),
        ]
        sent = 0
        for event in events[:MAX_PER_POLL]:
            outcome = await self._deliver(event)
            if outcome == RETRY_LATER:
                break
            event.apply(state)
            await self._save_state()
            if outcome == DELIVERED and event.text is not None:
                sent += 1
        return sent

    async def send_startup(self, fresh_baseline: bool) -> None:
        """Envia el mensaje de arranque con el estado de riesgo.

        Args:
            fresh_baseline: True si la linea base se tomo ahora (no se avisan eventos previos).

        Efectos:
            Red: `sendMessage` a Telegram.
        """
        state = self._state or await baseline_state(self.db)
        risk = state["risk"]
        lines = [
            "Notificaciones de Minerva ACTIVAS: aviso de posiciones abiertas y cerradas, cambios "
            "del universo y alertas de riesgo.",
            f"Estado: kill switch {'ACTIVO' if risk['kill'] else 'inactivo'} | scheduler "
            f"{'PAUSADO' if risk['paused'] else 'en marcha'} | circuit breaker "
            f"{'ACTIVO' if risk['breaker_until'] else 'inactivo'} | stop por drawdown "
            f"{'ACTIVO' if risk['drawdown_stop'] else 'inactivo'}",
            "Linea base tomada ahora: no se avisan los eventos anteriores." if fresh_baseline
            else "Se retoma desde el ultimo evento avisado.",
        ]
        await self._deliver(PushEvent("STARTUP", _message(*lines), lambda state: None))

    async def _space(self) -> None:
        wait = self._last_send + MIN_SPACING_S - self._monotonic()
        if wait > 0:
            await self._sleep(wait)

    def _record_failure(self, exc: BaseException) -> None:
        self.failed_total += 1
        self.consecutive_failures += 1
        self.last_error = type(exc).__name__ if not isinstance(exc, TelegramApiError) else (
            f"TelegramApiError{'' if exc.status_code is None else f' {exc.status_code}'}"
        )

    async def _deliver(self, event: PushEvent) -> str:
        if event.text is None:
            return DELIVERED
        for attempt in range(MAX_ATTEMPTS):
            await self._space()
            try:
                await asyncio.wait_for(
                    self.client.send_message(self.chat_id, event.text),
                    timeout=self._send_timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except TelegramApiError as exc:
                self._last_send = self._monotonic()
                self._record_failure(exc)
                if exc.status_code in PERMANENT_STATUS:
                    self.dropped_total += 1
                    logger.error("Notificaciones push: %s descartada (%s).", event.kind,
                                 self.last_error)
                    return DROPPED
                wait = exc.retry_after if exc.retry_after else RETRY_WAITS_S[attempt]
            except Exception as exc:  # noqa: BLE001 - red, tiempo agotado o un bug del cliente
                self._last_send = self._monotonic()
                self._record_failure(exc)
                wait = RETRY_WAITS_S[attempt]
            else:
                self._last_send = self._monotonic()
                self.sent_total += 1
                self.consecutive_failures = 0
                self.last_error = None
                self.last_sent_at = self._clock()
                return DELIVERED
            if attempt < MAX_ATTEMPTS - 1:
                await self._sleep(min(wait, MAX_WAIT_S))
        logger.warning("Notificaciones push: %s no se pudo enviar (%s); se reintenta luego.",
                       event.kind, self.last_error)
        return RETRY_LATER
