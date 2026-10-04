"""Monitor de posiciones (subfase 3.4): modo tick, liquidacion confirmada por REST,
antiguedad de ticks, latido del feed, equity periodico y reconciliacion al
reiniciar con velas 1m del periodo caido. Sin red: todo se siembra en la base o
se sirve desde un cliente REST falso."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.execution.paper_backend import PaperBackend
from app.persistence.models import OHLCVBar, Side, TradeStatus
from app.persistence.repositories import (
    funding_repo,
    health_repo,
    ohlcv_repo,
    system_state_repo,
    trades_repo,
)
from app.trading.levels import StrategyLevels
from app.trading.position_monitor import MONITOR_LAST_SEEN_KEY, PositionMonitor

BAR_MS = 60_000


class FakeRest:
    """Precio y marca controlables; cualquier llamada no prevista falla (ver `calls`)."""

    def __init__(self, last: float, mark: float | None = None) -> None:
        self.last = last
        self.mark = last if mark is None else mark
        self.calls: list[str] = []
        self.fail_tickers = False

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        self.calls.append("get_tickers")
        if self.fail_tickers:
            raise ConnectionError("REST caido (simulado)")
        return [{"symbol": symbol, "lastPrice": str(self.last), "markPrice": str(self.mark)}]

    async def get_funding_rate_history(self, *args, **kwargs):
        self.calls.append("get_funding_rate_history")
        return []

    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"llamada de red inesperada: {name}")

        return _fail


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006, MAKER_FEE_PCT=0.0002,
        INITIAL_CAPITAL_USDT=100.0, MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=5, MAX_SAME_DIRECTION_POSITIONS=5,
        LIVE_SL_MARGIN_CAP_PCT=50.0, MAX_DAILY_LOSS_PCT=0.50, MAX_DRAWDOWN_PCT=0.90,
        REAL_ACCOUNT_ELIGIBLE_STRATEGIES="ema_cross_9_21",
        BACKTEST_SLIPPAGE_BPS=0.0, TP_GAP_TOLERANCE_PCT=0.002, TICK_STALE_SECONDS=60.0,
        WS_STALE_AFTER_SECONDS=45.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


async def _open_long(backend, levels=None):
    """LONG a 100 con SL 95, TP 110 (liquidacion ~90.5 a 10x, MMR de respaldo)."""
    if levels is None:
        levels = StrategyLevels(stop_price=95.0, take_profit_price=110.0)
    return await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        sl_margin_loss_pct=50.0, levels=levels,
    )


async def _setup(db, last=100.0, mark=None, **settings_overrides):
    rest = FakeRest(last=last, mark=mark)
    settings = make_settings(**settings_overrides)
    backend = PaperBackend(db, rest, settings)
    monitor = PositionMonitor(db, rest, backend, settings)
    return rest, settings, backend, monitor


def _ws_ticker(symbol: str, price: float) -> dict:
    return {"ch": "tickers", "ts": 0, "data": [{"s": symbol, "la": str(price)}]}


# --- modo tick: SL, TP, trailing, liquidacion ---------------------------------


@pytest.mark.asyncio
async def test_tick_sequence_crossing_sl_closes_at_observed_price_with_sl_reason(db):
    """Plan 3.4: una secuencia de ticks que cruza el SL cierra la posicion con el
    precio observado en el tick y la razon SL (peor caso, sesgo conservador)."""
    rest, _settings, backend, monitor = await _setup(db)
    trade = await _open_long(backend)
    await monitor._reload_trades()

    for price in (99.0, 98.0, 96.0):
        await monitor.handle_ws_message(_ws_ticker("BTCUSDT", price))
    assert (await trades_repo.get_trade(db, trade.id)).status == TradeStatus.OPEN

    await monitor.handle_ws_message(_ws_ticker("BTCUSDT", 94.5))
    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.close_reason == "SL"
    assert closed.exit_price == pytest.approx(94.5)
    assert closed.fill_source == "TICK"


@pytest.mark.asyncio
async def test_tp_within_tolerance_fills_at_nominal_and_evident_gap_at_observed(db):
    rest, _s, backend, monitor = await _setup(db)
    near = await _open_long(backend)
    await monitor._reload_trades()
    await monitor.on_tick("BTCUSDT", 110.1)  # cruza TP 110 dentro de la tolerancia 0.2%
    closed = await trades_repo.get_trade(db, near.id)
    assert closed.close_reason == "TP"
    assert closed.exit_price == pytest.approx(110.0)

    gap = await _open_long(backend)
    await monitor._reload_trades()
    await monitor.on_tick("BTCUSDT", 115.0)  # hueco evidente: se rellena al observado
    closed_gap = await trades_repo.get_trade(db, gap.id)
    assert closed_gap.close_reason == "TP"
    assert closed_gap.exit_price == pytest.approx(115.0)


@pytest.mark.asyncio
async def test_trailing_moves_stop_up_and_triggers_trailing_reason(db):
    rest, _s, backend, monitor = await _setup(db)
    levels = StrategyLevels(stop_price=95.0, take_profit_price=None, trailing_distance=3.0)
    trade = await _open_long(backend, levels=levels)
    await monitor._reload_trades()

    await monitor.on_tick("BTCUSDT", 108.0)  # best 108 -> stop 105 (sube desde 95)
    still = await trades_repo.get_trade(db, trade.id)
    assert still.status == TradeStatus.OPEN

    await monitor.on_tick("BTCUSDT", 104.0)  # cruza el stop trailing (105)
    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.close_reason == "TRAILING"
    assert closed.exit_price == pytest.approx(104.0)


@pytest.mark.asyncio
async def test_liquidation_needs_rest_mark_confirmation(db):
    """El ultimo precio cruza la liquidacion (~90.5) pero el mark por REST NO
    confirma: no se liquida. Con mark confirmatorio, si."""
    rest, _s, backend, monitor = await _setup(db, last=100.0, mark=100.0)
    manual = await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, is_manual=True,
    )  # sin SL/TP: solo la liquidacion la puede cerrar
    await monitor._reload_trades()

    rest.mark = 95.0  # mark NO cruza la liquidacion
    await monitor.on_tick("BTCUSDT", 90.0)
    assert (await trades_repo.get_trade(db, manual.id)).status == TradeStatus.OPEN

    # Dentro de la ventana de reintento no se vuelve a consultar: sigue abierta.
    rest.mark = 90.0
    await monitor.on_tick("BTCUSDT", 90.0)
    assert (await trades_repo.get_trade(db, manual.id)).status == TradeStatus.OPEN

    monitor._liq_checked_at.clear()  # simula que pasa la ventana de reintento
    monitor._ticker_cache.clear()
    await monitor.on_tick("BTCUSDT", 90.0)
    liquidated = await trades_repo.get_trade(db, manual.id)
    assert liquidated.close_reason == "LIQUIDATION"
    assert liquidated.exit_price == pytest.approx(90.0)  # el mark confirmatorio


@pytest.mark.asyncio
async def test_liquidation_not_executed_when_rest_confirmation_fails(db):
    rest, _s, backend, monitor = await _setup(db)
    manual = await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, is_manual=True,
    )
    await monitor._reload_trades()
    rest.fail_tickers = True
    await monitor.on_tick("BTCUSDT", 89.0)
    assert (await trades_repo.get_trade(db, manual.id)).status == TradeStatus.OPEN


@pytest.mark.asyncio
async def test_tick_stale_symbol_is_evaluated_via_rest_price(db):
    """Sin ticks WS durante `TICK_STALE_SECONDS`, el monitor consulta el precio por
    REST y evalua el SL con el: el cierre sigue vivo aunque el WS calle."""
    rest, _s, backend, monitor = await _setup(db, TICK_STALE_SECONDS=60.0)
    trade = await _open_long(backend)
    rest.last = 94.0  # el REST devuelve un precio que cruza el SL
    await monitor.tick_periodic(now_ms=int(time.time() * 1000))
    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.close_reason == "SL"


@pytest.mark.asyncio
async def test_monitor_closes_work_even_when_feed_is_stale(db):
    """Nunca bloquea cierres: con el latido caido, el SL igual cierra la posicion."""
    rest, _s, backend, monitor = await _setup(db)
    trade = await _open_long(backend)
    await health_repo.record_success(
        db, health_repo.WS_FEED_SOURCE, datetime.now(UTC) - timedelta(seconds=600)
    )
    await monitor._reload_trades()
    await monitor.on_tick("BTCUSDT", 90.0)
    assert (await trades_repo.get_trade(db, trade.id)).close_reason == "SL"


# --- latido del feed y equity periodico ---------------------------------------


@pytest.mark.asyncio
async def test_any_ws_message_refreshes_the_feed_heartbeat(db):
    rest, _s, _backend, monitor = await _setup(db)
    await monitor.handle_ws_message({"op": "pong", "pong": 1})
    last = await health_repo.get_last_success(db, health_repo.WS_FEED_SOURCE)
    assert last is not None
    assert (datetime.now(UTC) - last).total_seconds() < 5


@pytest.mark.asyncio
async def test_periodic_tick_updates_equity_peak_and_daily_baseline(db):
    rest, _s, backend, monitor = await _setup(db)
    await _open_long(backend)
    # Primer periodo: precio plano, equity = capital inicial -> linea base del dia.
    await monitor.tick_periodic(now_ms=int(time.time() * 1000))
    assert await system_state_repo.get_state(db, "daily_loss_day") is not None
    baseline_before = await system_state_repo.get_state(db, "daily_loss_baseline_usdt")
    assert baseline_before is not None

    # El precio sube: el periodo siguiente debe elevar el pico de equity (sin que
    # un tick nuevo tenga que llegar por WS).
    rest.last = 110.0
    rest.mark = 110.0
    monitor._ticker_cache.clear()  # el siguiente ciclo (15 s) ya no ve la respuesta cacheada
    await monitor.tick_periodic(now_ms=int(time.time() * 1000))
    peak = float(await system_state_repo.get_state(db, "equity_peak_usdt"))
    assert peak > 100.0
    # La linea base del dia no se mueve dentro del mismo dia.
    assert await system_state_repo.get_state(db, "daily_loss_baseline_usdt") == baseline_before
    assert await system_state_repo.get_state(db, MONITOR_LAST_SEEN_KEY) is not None


@pytest.mark.asyncio
async def test_ws_tickers_feed_monitor_tick_counter(db):
    rest, _s, backend, monitor = await _setup(db)
    await handle_twice(monitor)
    assert monitor.ticks_received == 2


async def handle_twice(monitor):
    await monitor.handle_ws_message(_ws_ticker("ETHUSDT", 3000.0))
    await monitor.handle_ws_message(_ws_ticker("ETHUSDT", 3001.0))


# --- reconciliacion al reiniciar (velas 1m del periodo caido) -----------------


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _seed_1m(db, symbol: str, first_open: int, last_closed_open: int,
                   overrides: dict[int, dict] | None = None) -> None:
    """Velas 1m planas a 100 (LAST y MARK), salvo `overrides[open_time]` = campos."""
    overrides = overrides or {}
    for price_type in ("LAST_PRICE", "MARK_PRICE"):
        bars = []
        t = first_open
        while t <= last_closed_open:
            fields = {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
            fields.update(overrides.get(t, {}))
            bars.append(OHLCVBar(
                symbol=symbol, interval="1m", price_type=price_type, open_time=t,
                base_vol=1.0, quote_vol=1.0, **fields,
            ))
            t += BAR_MS
        await ohlcv_repo.upsert_bars(db, bars)


async def _open_downtime_trade(db, backend, opened_minutes_ago: int, last_seen_minutes_ago: int,
                               now_ms: int):
    trade = await _open_long(backend, levels=StrategyLevels(95.0, 110.0))
    opened = datetime.fromtimestamp((now_ms - opened_minutes_ago * BAR_MS) / 1000, tz=UTC)
    await db.execute("UPDATE trades SET opened_at = ? WHERE id = ?",
                     (opened.isoformat(), trade.id))
    await system_state_repo.set_state(
        db, MONITOR_LAST_SEEN_KEY, str(now_ms - last_seen_minutes_ago * BAR_MS)
    )
    return trade


def _window(now_ms: int, opened_minutes_ago: int):
    first_open = ((now_ms - opened_minutes_ago * BAR_MS) // BAR_MS) * BAR_MS
    last_closed_open = (now_ms // BAR_MS) * BAR_MS - BAR_MS
    return first_open, last_closed_open


@pytest.mark.asyncio
async def test_reconciliation_closes_at_sl_that_happened_during_downtime(db):
    """Plan 3.4: vela sintetica de SL durante una caida simulada cierra la posicion
    retroactivamente, antes de retomar ticks. Precio nominal (convencion del
    backtest) y fuente CANDLE_RECON."""
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, opened_minutes_ago=20,
                                       last_seen_minutes_ago=10, now_ms=now)
    first_open, last_closed = _window(now, 20)
    dip_at = ((now - 5 * BAR_MS) // BAR_MS) * BAR_MS  # 5 min antes de ahora, dentro de la caida
    await _seed_1m(db, "BTCUSDT", first_open, last_closed,
                   overrides={dip_at: {"low": 94.0, "close": 94.5}})

    outcomes = await monitor.reconcile_on_startup(now_ms=now)
    assert outcomes == [(trade.id, "CERRADA_SL")]
    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.exit_price == pytest.approx(95.0)  # nominal, no el minimo de la vela
    assert closed.fill_source == "CANDLE_RECON"
    assert closed.closed_at is not None and closed.closed_at.timestamp() * 1000 > dip_at


@pytest.mark.asyncio
async def test_reconciliation_closes_at_tp_during_downtime(db):
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, 20, 10, now)
    first_open, last_closed = _window(now, 20)
    spike = ((now - 4 * BAR_MS) // BAR_MS) * BAR_MS
    await _seed_1m(db, "BTCUSDT", first_open, last_closed,
                   overrides={spike: {"high": 111.0, "close": 110.5}})

    assert await monitor.reconcile_on_startup(now_ms=now) == [(trade.id, "CERRADA_TP")]
    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.exit_price == pytest.approx(110.0)
    assert closed.close_reason == "TP"


@pytest.mark.asyncio
async def test_reconciliation_liquidates_from_mark_bars_during_downtime(db):
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    manual = await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, is_manual=True,
    )
    opened = datetime.fromtimestamp((now - 20 * BAR_MS) / 1000, tz=UTC)
    await db.execute(
        "UPDATE trades SET opened_at = ? WHERE id = ?", (opened.isoformat(), manual.id)
    )
    await system_state_repo.set_state(db, MONITOR_LAST_SEEN_KEY, str(now - 10 * BAR_MS))
    first_open, last_closed = _window(now, 20)
    crash = ((now - 3 * BAR_MS) // BAR_MS) * BAR_MS
    # Solo el MARK cruza la liquidacion (~90.5); el ultimo precio no.
    bars_last = {crash: {}}
    await _seed_1m(db, "BTCUSDT", first_open, last_closed, overrides=bars_last)
    mark_bars = [OHLCVBar(symbol="BTCUSDT", interval="1m", price_type="MARK_PRICE",
                          open_time=crash, open=100.0, high=100.0, low=89.0, close=90.0,
                          base_vol=1.0, quote_vol=1.0)]
    await ohlcv_repo.upsert_bars(db, mark_bars)

    assert await monitor.reconcile_on_startup(now_ms=now) == [(manual.id, "CERRADA_LIQUIDATION")]
    closed = await trades_repo.get_trade(db, manual.id)
    assert closed.close_reason == "LIQUIDATION"
    assert closed.exit_price == pytest.approx(90.5)  # umbral nominal de liquidacion


@pytest.mark.asyncio
async def test_reconciliation_applies_funding_that_happened_during_downtime(db):
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, 20, 10, now)
    funding_at = now - 6 * BAR_MS  # evento de funding dentro de la caida
    await funding_repo.upsert_funding(db, "BTCUSDT", [(funding_at, 0.001)])
    first_open, last_closed = _window(now, 20)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)

    assert await monitor.reconcile_on_startup(now_ms=now) == [(trade.id, "ABIERTA")]
    still = await trades_repo.get_trade(db, trade.id)
    assert still.status == TradeStatus.OPEN
    # LONG paga: notional 100 * tasa 0.001 = 0.1 USDT, aplicado una sola vez.
    assert still.funding_paid_usdt == pytest.approx(0.1)
    assert still.funding_last_applied_ms == funding_at


@pytest.mark.asyncio
async def test_reconciliation_with_incomplete_candles_keeps_trade_open(db):
    """Sin velas completas no se fia del periodo: la posicion queda abierta y se
    registra CRITICAL; los ticks en vivo toman el control."""
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, 60, 50, now)
    first_open, last_closed = _window(now, 60)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)
    # Borra 10 velas del medio: falta mas de la tolerancia.
    await db.execute(
        "DELETE FROM ohlcv_cache WHERE symbol = ? AND interval = '1m' "
        "AND open_time BETWEEN ? AND ?",
        ("BTCUSDT", first_open + 20 * BAR_MS, first_open + 29 * BAR_MS),
    )
    outcomes = await monitor.reconcile_on_startup(now_ms=now)
    assert outcomes == [(trade.id, "ERROR")]
    assert (await trades_repo.get_trade(db, trade.id)).status == TradeStatus.OPEN


@pytest.mark.asyncio
async def test_reconciliation_skips_trades_with_no_downtime_window(db):
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, 20, 0, now)
    await system_state_repo.set_state(db, MONITOR_LAST_SEEN_KEY, str(now - 10_000))
    assert await monitor.reconcile_on_startup(now_ms=now) == [(trade.id, "SIN_VENTANA")]


# --- niveles al abrir --------------------------------------------------------


@pytest.mark.asyncio
async def test_open_rejects_sl_already_beyond_the_fill_price(db):
    rest, _s, backend, monitor = await _setup(db, last=100.0)
    with pytest.raises(ValueError, match="ya superado"):
        await backend.open_position(
            "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
            sl_margin_loss_pct=50.0, levels=StrategyLevels(stop_price=105.0),
        )


@pytest.mark.asyncio
async def test_open_records_liquidation_and_fallback_levels(db):
    rest, _s, backend, monitor = await _setup(db)
    trade = await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        sl_margin_loss_pct=50.0, levels=StrategyLevels(),
    )
    assert trade.sl_price == pytest.approx(95.0)  # respaldo 5%
    assert trade.tp_price == pytest.approx(110.0)  # respaldo 10%
    assert trade.liq_price == pytest.approx(90.5)  # 100*(1-0.1+0.005)
    assert trade.effective_stop == pytest.approx(95.0)
    assert trade.decision_source == "SIN_LLM"


@pytest.mark.asyncio
async def test_open_rejects_tp_on_the_wrong_side_of_the_fill(db):
    """Un TP del lado equivocado se cerraria en el primer tick: la entrada se descarta."""
    rest, _s, backend, monitor = await _setup(db, last=100.0)
    with pytest.raises(ValueError, match="TP planeado"):
        await backend.open_position(
            "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
            sl_margin_loss_pct=50.0, levels=StrategyLevels(stop_price=95.0, take_profit_price=90.0),
        )


@pytest.mark.asyncio
async def test_reconciliation_ignores_a_wick_in_the_candle_that_straddles_the_opening(db):
    """La posicion se abrio a mitad de una vela 1m: una mecha de esa vela ANTERIOR a
    la apertura no puede cerrarla (la posicion no existia). Sin la regla, esta
    prueba cerraria la posicion por SL en el minuto previo a su apertura."""
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    manual_levels = StrategyLevels(95.0, 110.0)
    trade = await _open_long(backend, levels=manual_levels)
    first_open, last_closed = _window(now, 20)
    opened_at_ms = first_open + 30_000  # abierta 30 s dentro del primer minuto
    await db.execute("UPDATE trades SET opened_at = ? WHERE id = ?",
                     (datetime.fromtimestamp(opened_at_ms / 1000, tz=UTC).isoformat(), trade.id))
    await system_state_repo.set_state(db, MONITOR_LAST_SEEN_KEY, str(now - 25 * BAR_MS))
    # Mecha que perfora el SL solo en el minuto que contiene la apertura, ANTES de abrir.
    await _seed_1m(db, "BTCUSDT", first_open, last_closed,
                   overrides={first_open: {"low": 94.0, "close": 94.5}})

    assert await monitor.reconcile_on_startup(now_ms=now) == [(trade.id, "ABIERTA")]
    assert (await trades_repo.get_trade(db, trade.id)).status == TradeStatus.OPEN


@pytest.mark.asyncio
async def test_one_ticker_request_per_symbol_per_cycle_is_shared_by_all_consumers(db):
    """Marcas, antiguedad de ticks y confirmacion de liquidacion piden el mismo ticker:
    dentro de la ventana de cache debe salir UNA sola consulta REST por simbolo."""
    rest, _s, backend, monitor = await _setup(db)
    await _open_long(backend)
    await monitor._reload_trades()
    rest.calls.clear()
    await monitor._check_tick_ages()   # sin ticks WS -> consulta REST
    await monitor._refresh_marks()     # mismo simbolo, dentro de la ventana
    assert rest.calls.count("get_tickers") == 1
