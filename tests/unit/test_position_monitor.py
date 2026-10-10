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
    shadow_repo,
    system_state_repo,
    trades_repo,
)
from app.trading.ai_value import ai_value_verdict
from app.trading.levels import StrategyLevels
from app.trading.position_monitor import (
    MAX_SHADOW_RECONCILE_ATTEMPTS,
    MONITOR_LAST_SEEN_KEY,
    PositionMonitor,
    _residual_gaps_are_tolerable,
)
from app.trading.shadow_book import ShadowBook
from app.trading.signal_generator import SignalCandidate

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

    async def get_kline(self, *args, **kwargs) -> list[dict]:
        """Vacio: cualquier hueco que `_verify_and_repair` intente reparar con un
        pedido estrecho sigue sin aparecer (no es el hallazgo de la seccion 2 de
        docs/FASE2_INTEGRIDAD_VELAS.md, es un hueco de verdad en estas pruebas)."""
        self.calls.append("get_kline")
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


async def _setup_with_shadow(db, last=100.0, mark=None, **settings_overrides):
    """Como `_setup`, con un `ShadowBook` adjunto -- lo exigen las pruebas de
    reconciliacion de sombras (Etapa 2 del incidente de estabilidad)."""
    rest = FakeRest(last=last, mark=mark)
    settings = make_settings(**settings_overrides)
    backend = PaperBackend(db, rest, settings)
    shadow = ShadowBook(db, settings)
    monitor = PositionMonitor(db, rest, backend, settings, shadow=shadow)
    return rest, settings, backend, monitor


def _candidate(side=Side.LONG, price=100.0, levels=None, symbol="BTCUSDT") -> SignalCandidate:
    strategies = ["ema_cross_9_21"]
    return SignalCandidate(
        symbol=symbol, side=side, candle_close_time=datetime(2026, 1, 1, 4, tzinfo=UTC),
        contributing_strategies=strategies, sl_margin_loss_pct=50.0,
        levels_by_strategy={s: (levels or StrategyLevels()) for s in strategies},
        price_by_strategy={s: price for s in strategies},
    )


async def _open_downtime_shadow(
    db, monitor, opened_minutes_ago: int, last_seen_minutes_ago: int, now_ms: int,
    levels=None,
):
    sim = await monitor.shadow.open_candidate(_candidate(levels=levels))
    opened = datetime.fromtimestamp((now_ms - opened_minutes_ago * BAR_MS) / 1000, tz=UTC)
    await db.execute(
        "UPDATE shadow_trades SET opened_at = ? WHERE id = ?", (opened.isoformat(), sim.id)
    )
    await system_state_repo.set_state(
        db, MONITOR_LAST_SEEN_KEY, str(now_ms - last_seen_minutes_ago * BAR_MS)
    )
    return sim


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


# --- Etapa 2 del incidente de estabilidad: tolerancia, marcado y reintentos --


def test_residual_gaps_are_tolerable_requires_isolation_cap_and_mark_confirmation():
    # Aislado y confirmado por MARK_PRICE: tolerable.
    assert _residual_gaps_are_tolerable([1000 * BAR_MS], {1000 * BAR_MS}, expected=3000)
    # Sin huecos: trivialmente tolerable.
    assert _residual_gaps_are_tolerable([], set(), expected=3000)
    # Mismo hueco, pero SIN vela en MARK_PRICE para sustituir: no hay con que.
    assert not _residual_gaps_are_tolerable([1000 * BAR_MS], set(), expected=3000)
    # Dos huecos contiguos (no aislados), aunque ambos esten en MARK_PRICE.
    contiguous = [1000 * BAR_MS, 1001 * BAR_MS]
    assert not _residual_gaps_are_tolerable(contiguous, set(contiguous), expected=3000)
    # Por encima del tope minimo (2) con una ventana chica (0,1 % de 100 < 2,
    # asi que manda el minimo fijo).
    many = [i * 2 * BAR_MS for i in range(3)]  # 3 huecos aislados entre si
    assert not _residual_gaps_are_tolerable(many, set(many), expected=100)
    # Con ventana grande el tope lo da la fraccion (0,1 %), no el minimo fijo:
    # expected=5000 -> tope=5.
    five = [i * 2 * BAR_MS for i in range(5)]
    assert _residual_gaps_are_tolerable(five, set(five), expected=5000)
    six = [i * 2 * BAR_MS for i in range(6)]
    assert not _residual_gaps_are_tolerable(six, set(six), expected=5000)


@pytest.mark.asyncio
async def test_reconciliation_substitutes_isolated_gap_with_mark_price_bar(db):
    """Etapa 2a: un hueco aislado de LAST_PRICE, confirmado por MARK_PRICE, se
    sustituye para evaluar SL/TP -- el cierre queda marcado con fill_source
    CANDLE_RECON_MARK_SUBSTITUTE en vez de dejar la reconciliacion en CRITICAL."""
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, opened_minutes_ago=20,
                                       last_seen_minutes_ago=10, now_ms=now)
    first_open, last_closed = _window(now, 20)
    dip_at = ((now - 5 * BAR_MS) // BAR_MS) * BAR_MS
    await _seed_1m(db, "BTCUSDT", first_open, last_closed,
                   overrides={dip_at: {"low": 94.0, "close": 94.5}})
    await db.execute(
        "DELETE FROM ohlcv_cache WHERE symbol = ? AND interval = '1m' "
        "AND price_type = 'LAST_PRICE' AND open_time = ?", ("BTCUSDT", dip_at),
    )

    outcomes = await monitor.reconcile_on_startup(now_ms=now)
    assert outcomes == [(trade.id, "CERRADA_SL")]
    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.exit_price == pytest.approx(95.0)  # nominal, igual que CANDLE_RECON
    assert closed.fill_source == "CANDLE_RECON_MARK_SUBSTITUTE"
    assert closed.reconciliation_failed is False


@pytest.mark.asyncio
async def test_reconciliation_failure_marks_the_trade_instead_of_closing_or_erroring_silently(db):
    """Un hueco grande y contiguo (fuera de toda tolerancia) sigue marcando la
    posicion como fallida -- ya no solo se registra CRITICAL y se olvida."""
    rest, _s, backend, monitor = await _setup(db)
    now = _now_ms()
    trade = await _open_downtime_trade(db, backend, 60, 50, now)
    first_open, last_closed = _window(now, 60)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)
    await db.execute(
        "DELETE FROM ohlcv_cache WHERE symbol = ? AND interval = '1m' "
        "AND open_time BETWEEN ? AND ?",
        ("BTCUSDT", first_open + 20 * BAR_MS, first_open + 29 * BAR_MS),
    )

    outcomes = await monitor.reconcile_on_startup(now_ms=now)
    assert outcomes == [(trade.id, "ERROR")]
    marked = await trades_repo.get_trade(db, trade.id)
    assert marked.status == TradeStatus.OPEN
    assert marked.reconciliation_failed is True
    assert marked.reconciliation_attempts == 1


@pytest.mark.asyncio
async def test_frozen_shadow_with_failed_reconciliation_is_not_closed_by_a_live_tick(db):
    rest, settings, backend, monitor = await _setup_with_shadow(db)
    sim = await monitor.shadow.open_candidate(_candidate(levels=StrategyLevels(95.0, 110.0)))
    await shadow_repo.update_reconciliation_state(db, sim.id, True, 1)
    await monitor._reload_trades()

    await monitor.on_tick("BTCUSDT", 90.0)  # cruzaria el SL si no estuviera congelada

    still = await shadow_repo.get(db, sim.id)
    assert still.status == TradeStatus.OPEN


@pytest.mark.asyncio
async def test_real_trade_tick_close_while_marked_uses_the_unreconciled_fill_source(db):
    """Una posicion REAL sigue vigilada en vivo aunque este marcada (nunca se
    congela): el cierre por tick queda distinguible con TICK_UNRECONCILED."""
    rest, _s, backend, monitor = await _setup(db)
    trade = await _open_long(backend)
    await trades_repo.update_reconciliation_state(db, trade.id, True, 1)
    await monitor._reload_trades()

    await monitor.on_tick("BTCUSDT", 94.5)

    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.fill_source == "TICK_UNRECONCILED"


@pytest.mark.asyncio
async def test_retry_reconciliation_success_clears_the_failed_flag(db):
    rest, settings, backend, monitor = await _setup_with_shadow(db)
    now = _now_ms()
    sim = await _open_downtime_shadow(db, monitor, opened_minutes_ago=20,
                                      last_seen_minutes_ago=10, now_ms=now,
                                      levels=StrategyLevels(95.0, 110.0))
    await shadow_repo.update_reconciliation_state(db, sim.id, True, 1)
    first_open, last_closed = _window(now, 20)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)  # ahora las velas SI estan completas
    await monitor._reload_trades()

    await monitor._retry_failed_reconciliations(now)

    cleared = await shadow_repo.get(db, sim.id)
    assert cleared.status == TradeStatus.OPEN
    assert cleared.reconciliation_failed is False
    assert cleared.reconciliation_attempts == 0


def _irreparable_gap(db, symbol: str, first_open: int):
    """Hueco contiguo de 10 velas, siempre fuera de la tolerancia aislada de la
    Etapa 2a -- usado para forzar una falla de reconciliacion determinista."""
    return db.execute(
        "DELETE FROM ohlcv_cache WHERE symbol = ? AND interval = '1m' "
        "AND open_time BETWEEN ? AND ?",
        (symbol, first_open + 20 * BAR_MS, first_open + 29 * BAR_MS),
    )


@pytest.mark.asyncio
async def test_shadow_does_not_give_up_after_max_attempts_if_little_time_elapsed(db):
    """Correccion 2 (revision de Etapa 2): 3 intentos cada 300 s son ~10 min -- un
    corte de esa duracion no debe descartar una sombra sana. Reloj controlado:
    los 3 intentos ocurren dentro de una hora, muy por debajo de
    `MIN_SHADOW_RECONCILE_GIVE_UP_HOURS` (6h), asi que NO debe rendirse."""
    rest, settings, backend, monitor = await _setup_with_shadow(db)
    now = _now_ms()
    sim = await _open_downtime_shadow(db, monitor, opened_minutes_ago=60,
                                      last_seen_minutes_ago=50, now_ms=now)
    first_open, last_closed = _window(now, 60)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)
    await _irreparable_gap(db, "BTCUSDT", first_open)
    await monitor._reload_trades()
    key = monitor._key(sim)

    for hours in (0, 0.5, 0.9):  # tres intentos dentro de la misma hora
        await monitor._attempt_reconcile_retry(
            monitor._trades[key], now + int(hours * 3_600_000)
        )

    still = await shadow_repo.get(db, sim.id)
    assert still.status == TradeStatus.OPEN
    assert still.reconciliation_failed is True
    assert still.reconciliation_attempts == MAX_SHADOW_RECONCILE_ATTEMPTS
    assert key in monitor._trades


@pytest.mark.asyncio
async def test_shadow_closes_as_reconcile_failed_after_max_attempts_and_enough_elapsed_time(db):
    """Con el minimo de intentos Y de horas de racha fallida cumplidos (reloj
    controlado: la tercera falla llega 7h despues de la primera), la sombra se
    cierra sin PnL."""
    rest, settings, backend, monitor = await _setup_with_shadow(db)
    now = _now_ms()
    sim = await _open_downtime_shadow(db, monitor, opened_minutes_ago=60,
                                      last_seen_minutes_ago=50, now_ms=now)
    first_open, last_closed = _window(now, 60)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)
    await _irreparable_gap(db, "BTCUSDT", first_open)
    await monitor._reload_trades()
    key = monitor._key(sim)

    first_attempt_at = now
    await monitor._attempt_reconcile_retry(monitor._trades[key], first_attempt_at)
    window_after_first = monitor._trades[key].reconciliation_window_start_ms
    await monitor._attempt_reconcile_retry(
        monitor._trades[key], first_attempt_at + 3 * 3_600_000
    )
    # La ventana desde donde reintentar no se mueve entre fallas sucesivas.
    assert monitor._trades[key].reconciliation_window_start_ms == window_after_first
    await monitor._attempt_reconcile_retry(
        monitor._trades[key], first_attempt_at + 7 * 3_600_000
    )

    closed = await shadow_repo.get(db, sim.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.close_reason == "RECONCILE_FAILED"
    assert closed.fill_source == "RECONCILE_FAILED"
    assert closed.pnl_gross_usdt == pytest.approx(0.0)
    assert closed.pnl_net_usdt == pytest.approx(0.0)
    assert closed.exit_price == pytest.approx(closed.entry_price)
    assert key not in monitor._trades


@pytest.mark.asyncio
async def test_retry_resumes_from_the_original_failure_window_not_from_opened_at(db):
    """Correccion 1 (revision de Etapa 2): un trailing que YA habia avanzado
    ANTES de la falla deja `effective_stop` mas ajustado que al abrir. Si el
    reintento rejugara desde `opened_at` (el bug reportado), una mecha vieja
    -- de ANTES de que el stop avanzara -- cruzaria el stop de HOY y cerraria
    la sombra por error. Con la ventana persistida desde la falla original,
    esa mecha vieja ni se mira."""
    rest, settings, backend, monitor = await _setup_with_shadow(db)
    now = _now_ms()
    sim = await _open_downtime_shadow(db, monitor, opened_minutes_ago=60,
                                      last_seen_minutes_ago=10, now_ms=now,
                                      levels=StrategyLevels(95.0, 110.0))
    # El trailing ya habia avanzado el stop a 99 ANTES de la falla (price action
    # favorable ya reconciliada/ticada con exito, no parte de la caida).
    await shadow_repo.update_risk_state(db, sim.id, 99.0, 104.0)
    first_open, last_closed = _window(now, 60)
    # Mecha vieja (minuto -50, bien antes de last_seen_ms=-10) que cruza el
    # stop de HOY (99) pero NO el original (95): si se rejugara desde
    # opened_at, cerraria por SL; no deberia ni evaluarse.
    old_wick_at = first_open + 10 * BAR_MS
    await _seed_1m(db, "BTCUSDT", first_open, last_closed,
                   overrides={old_wick_at: {"low": 96.0, "close": 96.5}})
    # Hueco contiguo DENTRO de la ventana reciente (last_seen_ms=-10min..ahora),
    # para que la PRIMERA reconciliacion (que ya parte de ahi, no de opened_at)
    # falle de verdad.
    gap_end = last_closed
    gap_start = last_closed - 3 * BAR_MS
    await db.execute(
        "DELETE FROM ohlcv_cache WHERE symbol = 'BTCUSDT' AND interval = '1m' "
        "AND open_time BETWEEN ? AND ?", (gap_start, gap_end),
    )

    outcomes = await monitor.reconcile_on_startup(now_ms=now)
    assert outcomes == [(sim.id, "ERROR")]
    marked = await shadow_repo.get(db, sim.id)
    assert marked.reconciliation_window_start_ms is not None
    assert marked.reconciliation_window_start_ms > old_wick_at  # la mecha queda afuera

    # Se repara (upsert) el hueco que hizo fallar el primer intento; el
    # reintento ya puede completar la ventana (que NUNCA incluyo la mecha vieja).
    await _seed_1m(db, "BTCUSDT", first_open, last_closed,
                   overrides={old_wick_at: {"low": 96.0, "close": 96.5}})

    await monitor._reload_trades()
    key = monitor._key(sim)
    await monitor._attempt_reconcile_retry(monitor._trades[key], now)

    still_open = await shadow_repo.get(db, sim.id)
    assert still_open.status == TradeStatus.OPEN
    assert still_open.reconciliation_failed is False
    assert still_open.close_reason is None


@pytest.mark.asyncio
async def test_end_to_end_failed_reconciliation_freezes_shadow_and_excludes_it_from_verdict(db):
    """Caso de punta a punta pedido en la Etapa 2: reconciliacion falla -> sombra
    marcada y SIN cierre por tick -> excluida del veredicto de ai_value_verdict."""
    rest, settings, backend, monitor = await _setup_with_shadow(db)
    now = _now_ms()
    sim = await _open_downtime_shadow(db, monitor, opened_minutes_ago=60,
                                      last_seen_minutes_ago=50, now_ms=now,
                                      levels=StrategyLevels(95.0, 110.0))
    first_open, last_closed = _window(now, 60)
    await _seed_1m(db, "BTCUSDT", first_open, last_closed)
    await db.execute(
        "DELETE FROM ohlcv_cache WHERE symbol = ? AND interval = '1m' "
        "AND open_time BETWEEN ? AND ?",
        ("BTCUSDT", first_open + 20 * BAR_MS, first_open + 29 * BAR_MS),
    )

    outcomes = await monitor.reconcile_on_startup(now_ms=now)
    assert outcomes == [(sim.id, "ERROR")]

    await monitor.on_tick("BTCUSDT", 90.0)  # cruzaria el SL en vivo; no debe cerrarla
    marked = await shadow_repo.get(db, sim.id)
    assert marked.status == TradeStatus.OPEN
    assert marked.reconciliation_failed is True

    unreliable = await shadow_repo.get_unreliable_signal_group_keys(db)
    assert marked.signal_group_key in unreliable
    verdict = ai_value_verdict([marked], unreliable_keys=unreliable)
    assert verdict.total_groups == 0


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
