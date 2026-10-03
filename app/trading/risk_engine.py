"""Motor de riesgo EN VIVO (Fase 3, subfase 3.2, punto 4 de
`docs/FASE3_PLAN.md`) -- decide si una entrada NUEVA se puede abrir.

**Regla central (pedida explicitamente): este motor SOLO bloquea entradas
nuevas.** Nunca bloquea el cierre de una posicion ya abierta ni la
ejecucion de su SL/TP/liquidacion -- esos siguen su curso normal sin pasar
por `check_new_entry` en ningun punto. Si el kill switch, el circuit
breaker o el stop por drawdown estan activos, una posicion ya abierta
sigue gestionandose hasta que cierra por su propia logica (SL/TP/
liquidacion/manual); lo unico que cambia es que no se abren posiciones
nuevas.

**Limites que puede bloquear** (todos auditados en `risk_rejections` via
`risk_rejections_repo` -- cada rechazo guarda el motivo y los valores
exactos involucrados):
- `KILL_SWITCH_ACTIVE` -- interruptor manual, resume manual
  (`deactivate_kill_switch`).
- `DRAWDOWN_STOP_ACTIVE` -- solo en modo `DRAWDOWN_STOP_MODE="duro"`,
  resume manual (`resume_drawdown_stop`). En modo `"alerta"` nunca bloquea,
  pero el contador `drawdown_stop_would_have_triggered_count` sigue
  subiendo igual (ver `update_equity_tracking`).
- `CIRCUIT_BREAKER_ACTIVE` -- N perdidas consecutivas seguidas
  (`consecutive_losses_circuit_breaker`), auto-resume tras
  `circuit_breaker_cooldown_hours` (sin accion manual).
- `DAILY_LOSS_LIMIT` -- perdida del dia (ver abajo) >= `max_daily_loss_pct`.
- `STRATEGY_NOT_ELIGIBLE` -- la estrategia no esta en
  `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` (vacio = todas elegibles). **Corregido**:
  `strategy=None` ya NO salta este filtro por defecto -- solo lo salta
  cuando la entrada se marca explicitamente `is_manual=True` (un humano
  tecleando una orden). Antes cualquier entrada con `strategy=None` (incluida
  una generada por el pipeline automatico que perdiera el nombre de su
  estrategia por un bug) se colaba sin pasar por la lista de elegibilidad;
  ahora una entrada no-manual con `strategy=None` se evalua contra la lista
  igual que cualquier otra (y, si la lista no esta vacia, `None` nunca
  pertenece a ella -> se rechaza).
- `MAX_SIMULTANEOUS_POSITIONS` -- cupo total de posiciones abiertas.
- `MAX_SAME_DIRECTION_POSITIONS` -- cupo de posiciones en la MISMA
  direccion (LONG o SHORT), para acotar el riesgo de correlacion entre
  altcoins que `docs/FASE2_RIESGO.md` no puede medir.
- `SL_CAP_EXCEEDED` -- si `sl_margin_loss_pct` (riesgo planeado al abrir)
  supera `LIVE_SL_MARGIN_CAP_PCT`. **Corregido**: ya no es opcional para
  entradas no manuales -- `check_new_entry` lanza `ValueError` de inmediato
  (no un rechazo auditado, es un bug del llamador) si una entrada con
  `is_manual=False` no trae `sl_margin_loss_pct`. Solo las entradas
  `is_manual=True` pueden omitirlo.
- `MARGIN_UNAVAILABLE` -- el margen maximo permitido para esta entrada
  (`Settings.max_margin_for_new_trade`) calcula a <= 0.

**Perdida diaria y drawdown sobre equity, no solo capital realizado**
(pedido explicitamente): `current_equity` (pasado por el llamador, que es
quien puede consultar precios de mercado -- este modulo no toca la red)
debe ser capital realizado MAS PnL flotante de las posiciones abiertas,
nunca solo lo realizado -- una cuenta con posiciones abiertas perdiendo
fuerte no debe parecer "sana" solo porque nada se ha cerrado todavia. El
"dia" para la perdida diaria se define en la zona horaria configurable
`Settings.report_timezone` (default `America/Santiago`, ya usada para
reportes desde Fase 0) -- la linea base del dia se fija al cambio de dia
local con el equity de ESE instante (el primer dato disponible del dia
nuevo). **Corregido**: el bloqueo por `DAILY_LOSS_LIMIT` ahora queda
"enganchado" (persistido en `system_state`) la primera vez que se cruza el
umbral, y dura hasta el cambio de dia local -- antes se recalculaba el %
de perdida contra el equity actual en cada consulta, asi que una
recuperacion del PnL flotante dentro del MISMO dia lo desactivaba sola, sin
esperar al dia siguiente.

**Persistencia entre reinicios** (pedido explicitamente): todo el estado
(kill switch, drawdown stop, circuit breaker, racha de perdidas, pico de
equity, linea base del dia) vive en la tabla `system_state` (ya existe
desde Fase 1) -- este modulo nunca guarda nada en memoria del proceso, asi
que un reinicio del bot lee exactamente el mismo estado que habia antes de
apagarse, sin "olvidar" una pausa activa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.persistence.database import Database
from app.persistence.models import Side
from app.persistence.repositories import risk_rejections_repo, system_state_repo, trades_repo

# Claves de `system_state` usadas por este modulo.
_KEY_KILL_SWITCH = "kill_switch_active"
_KEY_DRAWDOWN_STOP_ACTIVE = "drawdown_stop_active"
_KEY_DRAWDOWN_BREACH_IN_PROGRESS = "drawdown_breach_in_progress"
_KEY_DRAWDOWN_TRIGGER_COUNT = "drawdown_stop_would_have_triggered_count"
_KEY_EQUITY_PEAK = "equity_peak_usdt"
_KEY_CONSECUTIVE_LOSSES = "consecutive_losses"
_KEY_CIRCUIT_BREAKER_UNTIL = "circuit_breaker_until"
_KEY_DAILY_LOSS_DAY = "daily_loss_day"
_KEY_DAILY_LOSS_BASELINE = "daily_loss_baseline_usdt"
_KEY_DAILY_LOSS_LOCKED = "daily_loss_limit_locked"
_KEY_DRAWDOWN_STOP_LAST_RESUMED_AT = "drawdown_stop_last_resumed_at"


class RiskRejectedError(RuntimeError):
    """Una entrada nueva fue bloqueada por el motor de riesgo. `reason` es
    uno de los codigos documentados en el docstring del modulo; `details`
    trae los valores exactos (ya quedo registrado en `risk_rejections`)."""

    def __init__(self, reason: str, details: dict) -> None:
        super().__init__(f"Entrada rechazada por el motor de riesgo: {reason} ({details})")
        self.reason = reason
        self.details = details


@dataclass
class RiskDecision:
    allowed: bool
    reason: str | None = None
    details: dict = field(default_factory=dict)
    # Solo significativo cuando `allowed` es True: el margen que de verdad
    # se debe usar (puede ser menor al solicitado, ver `Settings.
    # max_margin_for_new_trade` -- un ajuste, no un rechazo).
    approved_margin_usdt: float | None = None


# --- helpers de system_state (todo persiste ahi, nunca en memoria) -----


async def _get_bool(db: Database, key: str, default: bool) -> bool:
    raw = await system_state_repo.get_state(db, key)
    return default if raw is None else raw == "1"


async def _set_bool(db: Database, key: str, value: bool) -> None:
    await system_state_repo.set_state(db, key, "1" if value else "0")


async def _get_float(db: Database, key: str, default: float) -> float:
    raw = await system_state_repo.get_state(db, key)
    return default if raw is None else float(raw)


async def _set_float(db: Database, key: str, value: float) -> None:
    await system_state_repo.set_state(db, key, repr(value))


async def _get_int(db: Database, key: str, default: int) -> int:
    raw = await system_state_repo.get_state(db, key)
    return default if raw is None else int(raw)


async def _set_int(db: Database, key: str, value: int) -> None:
    await system_state_repo.set_state(db, key, str(value))


def today_key(tz_name: str, now: datetime | None = None) -> str:
    """Fecha local (YYYY-MM-DD) en la zona horaria `tz_name` -- define el
    limite de "dia" para la perdida diaria. `now` es inyectable para
    tests; por defecto usa la hora real."""
    now = now or datetime.now(UTC)
    return now.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


# --- trackers de equity (pico/drawdown, linea base del dia) -------------


async def update_equity_tracking(db: Database, settings: Settings, current_equity: float) -> None:
    """Actualiza el pico historico de equity (para el drawdown) y la linea
    base del dia (para la perdida diaria) -- se llama tanto al evaluar una
    entrada nueva como al cerrar una operacion (`record_trade_closed`),
    para que estos numeros esten al dia incluso antes de que el monitor de
    posiciones en vivo (subfase 3.4) corra de forma continua."""
    peak = await _get_float(db, _KEY_EQUITY_PEAK, settings.initial_capital_usdt)
    if current_equity > peak:
        peak = current_equity
        await _set_float(db, _KEY_EQUITY_PEAK, peak)

    drawdown_pct = (peak - current_equity) / peak if peak > 0 else 0.0
    breaching = drawdown_pct >= settings.max_drawdown_pct
    was_breaching = await _get_bool(db, _KEY_DRAWDOWN_BREACH_IN_PROGRESS, False)
    if breaching and not was_breaching:
        count = await _get_int(db, _KEY_DRAWDOWN_TRIGGER_COUNT, 0)
        await _set_int(db, _KEY_DRAWDOWN_TRIGGER_COUNT, count + 1)
        if settings.drawdown_stop_mode == "duro":
            await _set_bool(db, _KEY_DRAWDOWN_STOP_ACTIVE, True)
    await _set_bool(db, _KEY_DRAWDOWN_BREACH_IN_PROGRESS, breaching)

    today = today_key(settings.report_timezone)
    stored_day = await system_state_repo.get_state(db, _KEY_DAILY_LOSS_DAY)
    if stored_day != today:
        # Cambio de dia: la linea base se fija con el equity de ESTE
        # instante (el primer dato disponible del dia nuevo, sea por un
        # chequeo de entrada o por el cierre de una operacion) -- nunca con
        # un valor cacheado de una consulta anterior. El bloqueo de
        # perdida diaria del dia que termino se LIBERA aqui (bug corregido:
        # antes `_daily_loss_breached` recalculaba el % cada vez contra el
        # equity actual, asi que una recuperacion del PnL flotante dentro
        # del MISMO dia lo desactivaba solo, sin que hiciera falta esperar
        # al dia siguiente).
        await system_state_repo.set_state(db, _KEY_DAILY_LOSS_DAY, today)
        await _set_float(db, _KEY_DAILY_LOSS_BASELINE, current_equity)
        await _set_bool(db, _KEY_DAILY_LOSS_LOCKED, False)
    else:
        baseline = await _get_float(db, _KEY_DAILY_LOSS_BASELINE, current_equity)
        if baseline > 0:
            loss_pct = (baseline - current_equity) / baseline
            if loss_pct >= settings.max_daily_loss_pct:
                # Una vez que se cruza el umbral, el bloqueo queda "enganchado"
                # (persistido) por el resto del dia -- nunca se vuelve a
                # poner en False por una recuperacion del equity, solo por
                # el cambio de dia de arriba.
                await _set_bool(db, _KEY_DAILY_LOSS_LOCKED, True)


async def _daily_loss_breached(db: Database) -> bool:
    """Lee el enganche persistente de `update_equity_tracking` -- NUNCA
    recalcula el % contra el equity actual aqui, para que una recuperacion
    del PnL flotante dentro del mismo dia no libere el bloqueo (ver el
    comentario en `update_equity_tracking`)."""
    return await _get_bool(db, _KEY_DAILY_LOSS_LOCKED, False)


# --- circuit breaker: se actualiza al cerrar una operacion --------------


async def record_trade_closed(
    db: Database, settings: Settings, pnl_net_usdt: float, current_equity: float
) -> None:
    """Actualiza la racha de perdidas consecutivas (y dispara el circuit
    breaker si corresponde) y los trackers de equity. Se llama DESPUES de
    cerrar una operacion -- nunca bloquea el cierre en si, solo actualiza
    el estado que `check_new_entry` leera la proxima vez."""
    if pnl_net_usdt < 0:
        count = await _get_int(db, _KEY_CONSECUTIVE_LOSSES, 0) + 1
        await _set_int(db, _KEY_CONSECUTIVE_LOSSES, count)
        if count >= settings.consecutive_losses_circuit_breaker:
            until = datetime.now(UTC) + timedelta(hours=settings.circuit_breaker_cooldown_hours)
            await system_state_repo.set_state(db, _KEY_CIRCUIT_BREAKER_UNTIL, until.isoformat())
            # La racha que disparo el breaker ya quedo "consumida" -- una
            # sola perdida tras el enfriamiento no debe volver a dispararlo
            # de inmediato, necesita una racha nueva de N perdidas.
            await _set_int(db, _KEY_CONSECUTIVE_LOSSES, 0)
    else:
        await _set_int(db, _KEY_CONSECUTIVE_LOSSES, 0)
    await update_equity_tracking(db, settings, current_equity)


# --- controles manuales (persisten por construccion, ver docstring) ----


async def activate_kill_switch(db: Database) -> None:
    await _set_bool(db, _KEY_KILL_SWITCH, True)


async def deactivate_kill_switch(db: Database) -> None:
    await _set_bool(db, _KEY_KILL_SWITCH, False)


async def resume_drawdown_stop(db: Database, current_equity: float) -> None:
    """Reanudacion manual del stop por drawdown (modo `"duro"`) -- a
    diferencia del circuit breaker, este nunca se levanta solo.

    **Corregido** (antes esta funcion solo apagaba el flag activo): el pico
    historico de equity se REINICIA al equity actual y la marca de "brecha
    en curso" se limpia -- sin esto, tras reanudar, el drawdown seguia
    calculandose por encima del umbral contra el pico VIEJO, y como
    `update_equity_tracking` solo dispara el stop en el flanco de subida
    (`breaching and not was_breaching`), nunca volvia a activarse hasta que
    el equity se recuperara por completo al pico viejo -- en la practica,
    el stop quedaba inerte para el resto de la vida de la cuenta. El
    contador `drawdown_stop_would_have_triggered_count` (criterio de paso a
    dinero real) NO se toca aqui -- es deliberadamente una metrica aparte
    del drawdown acumulado desde el pico HISTORICO, nunca reiniciada por una
    reanudacion manual. Se registra el instante de la reanudacion en
    `system_state` para poder auditarlo."""
    await _set_bool(db, _KEY_DRAWDOWN_STOP_ACTIVE, False)
    await _set_bool(db, _KEY_DRAWDOWN_BREACH_IN_PROGRESS, False)
    await _set_float(db, _KEY_EQUITY_PEAK, current_equity)
    await system_state_repo.set_state(
        db, _KEY_DRAWDOWN_STOP_LAST_RESUMED_AT, datetime.now(UTC).isoformat()
    )


async def get_drawdown_stop_trigger_count(db: Database) -> int:
    """Cuantas veces el drawdown cruzo el umbral, en CUALQUIER modo -- usa
    esto (no el modo) para el criterio de paso a dinero real (punto 8 de
    `docs/FASE3_PLAN.md`), para que correr en `"alerta"` no haga parecer la
    cuenta mas segura de lo que realmente fue."""
    return await _get_int(db, _KEY_DRAWDOWN_TRIGGER_COUNT, 0)


# --- decision principal --------------------------------------------------


async def _reject(
    db: Database, symbol: str, side: Side, strategy: str | None, reason: str, details: dict
) -> RiskDecision:
    await risk_rejections_repo.insert_rejection(db, symbol, side, strategy, reason, details)
    return RiskDecision(allowed=False, reason=reason, details=details)


async def check_new_entry(
    db: Database,
    settings: Settings,
    *,
    symbol: str,
    side: Side,
    strategy: str | None,
    margin_usdt: float,
    current_equity: float,
    sl_margin_loss_pct: float | None = None,
    is_manual: bool = False,
) -> RiskDecision:
    """Decide si una entrada nueva se puede abrir. Nunca evalua cierres --
    ver el docstring del modulo. Actualiza los trackers de equity primero
    (para que el drawdown/perdida diaria reflejen `current_equity` aunque
    esta entrada termine rechazada por otro motivo).

    `is_manual` (nuevo) distingue una entrada tecleada por el usuario (sin
    estrategia, nunca sujeta a la lista de elegibilidad ni al SL
    obligatorio) de una entrada generada por el pipeline automatico
    (generador de senales, Fase 3 subfase 3.3) que simplemente perdio el
    nombre de su estrategia por un bug -- antes `strategy=None` saltaba la
    lista de elegibilidad SIEMPRE, lo que habria dejado pasar ese bug sin
    aviso. `sl_margin_loss_pct` es obligatorio para toda entrada NO manual
    (`ValueError`, no un rechazo auditado -- es un error de programacion
    del llamador, no una decision de riesgo)."""
    if not is_manual and sl_margin_loss_pct is None:
        raise ValueError(
            "sl_margin_loss_pct es obligatorio para entradas generadas por una estrategia "
            f"(symbol={symbol}, strategy={strategy!r}); solo las entradas manuales "
            "(is_manual=True) pueden omitirlo."
        )

    await update_equity_tracking(db, settings, current_equity)

    if await _get_bool(db, _KEY_KILL_SWITCH, False):
        return await _reject(db, symbol, side, strategy, "KILL_SWITCH_ACTIVE", {})

    if settings.drawdown_stop_mode == "duro" and await _get_bool(
        db, _KEY_DRAWDOWN_STOP_ACTIVE, False
    ):
        return await _reject(
            db, symbol, side, strategy, "DRAWDOWN_STOP_ACTIVE",
            {"max_drawdown_pct": settings.max_drawdown_pct},
        )

    cb_until_raw = await system_state_repo.get_state(db, _KEY_CIRCUIT_BREAKER_UNTIL)
    if cb_until_raw and datetime.now(UTC) < datetime.fromisoformat(cb_until_raw):
        return await _reject(
            db, symbol, side, strategy, "CIRCUIT_BREAKER_ACTIVE", {"until": cb_until_raw},
        )

    if await _daily_loss_breached(db):
        return await _reject(
            db, symbol, side, strategy, "DAILY_LOSS_LIMIT",
            {"max_daily_loss_pct": settings.max_daily_loss_pct},
        )

    eligible = settings.real_account_eligible_strategies_list
    if not is_manual and eligible and strategy not in eligible:
        return await _reject(
            db, symbol, side, strategy, "STRATEGY_NOT_ELIGIBLE", {"eligible": eligible},
        )

    open_positions = await trades_repo.get_open_positions(db)
    if len(open_positions) >= settings.max_simultaneous_positions:
        return await _reject(
            db, symbol, side, strategy, "MAX_SIMULTANEOUS_POSITIONS",
            {"open": len(open_positions), "limit": settings.max_simultaneous_positions},
        )

    same_direction = sum(1 for p in open_positions if p.side == side)
    if same_direction >= settings.max_same_direction_positions:
        return await _reject(
            db, symbol, side, strategy, "MAX_SAME_DIRECTION_POSITIONS",
            {"same_direction_open": same_direction,
             "limit": settings.max_same_direction_positions},
        )

    if sl_margin_loss_pct is not None and sl_margin_loss_pct > settings.live_sl_margin_cap_pct:
        return await _reject(
            db, symbol, side, strategy, "SL_CAP_EXCEEDED",
            {"sl_margin_loss_pct": sl_margin_loss_pct, "cap_pct": settings.live_sl_margin_cap_pct},
        )

    committed_symbol = await trades_repo.committed_margin(db, symbol)
    committed_total = await trades_repo.committed_margin(db)
    max_allowed = settings.max_margin_for_new_trade(
        current_capital=current_equity,
        margin_committed_on_symbol=committed_symbol,
        margin_committed_total=committed_total,
    )
    if max_allowed <= 0:
        return await _reject(
            db, symbol, side, strategy, "MARGIN_UNAVAILABLE",
            {"committed_total": committed_total, "equity": current_equity},
        )

    return RiskDecision(
        allowed=True, approved_margin_usdt=min(margin_usdt, max_allowed),
    )
