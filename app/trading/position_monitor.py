"""Monitor de posiciones en vivo (Fase 3, subfase 3.4, `docs/FASE3_PLAN.md` puntos
3 y 7). Es el UNICO que cierra posiciones por SL, TP, trailing y liquidacion:
el generador de senales solo abre.

Reglas de relleno (decididas con el usuario):
- SL/trailing en modo TICK: precio observado en el tick que lo cruza (peor caso).
- TP en modo TICK: nominal, salvo hueco evidente (`tp_tick_fill_price`).
- Liquidacion: el ultimo precio debe cruzar el umbral Y el mark por REST debe
  confirmarlo; si no confirma (o la consulta falla) NO se liquida.
- Reconciliacion (velas 1m del periodo caido, `CANDLE_RECON`): precio nominal, la
  misma convencion que el backtest.
Todo cierre se registra con `fill_source` (TICK, CANDLE_RECON o REST_MARK).

Nunca bloquea cierres. Solo el motor de riesgo bloquea aperturas, y lo hace
cuando el latido del feed (`ws_feed`) esta obsoleto.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime

from app.backtesting.liquidation import compute_liquidation_price
from app.config import Settings
from app.core.logging import get_logger
from app.execution.paper_backend import PaperBackend
from app.market.bitunix_rest import BitunixRestClient
from app.market.funding_history import download_missing_funding
from app.market.ohlcv_history import download_missing
from app.persistence.database import Database
from app.persistence.models import Side, Trade
from app.persistence.repositories import (
    health_repo,
    ohlcv_repo,
    specs_repo,
    system_state_repo,
    trades_repo,
)
from app.trading import risk_engine
from app.trading.funding_accrual import accrue_funding
from app.trading.stop_engine import (
    advance_trailing_stop,
    check_adverse_bar,
    check_adverse_tick,
    check_favorable_tp_bar,
    check_favorable_tp_tick,
    order_adverse_thresholds,
    tp_tick_fill_price,
)

logger = get_logger(__name__)

FEED_PERSIST_SECONDS = 5.0
FUNDING_REFRESH_SECONDS = 600.0
LIQ_CONFIRM_THROTTLE_SECONDS = 5.0
MONITOR_LAST_SEEN_KEY = "monitor_last_seen_ms"
BAR_MS = 60_000
RECONCILE_MIN_WINDOW_MS = BAR_MS
# Velas 1m que pueden faltar en la reconciliacion antes de no fiar en el periodo.
RECONCILE_MAX_MISSING_BARS = 2

FILL_TICK = "TICK"
FILL_CANDLE = "CANDLE_RECON"
FILL_REST = "REST_MARK"


def _unbounded(is_long: bool) -> float:
    """Umbral que nunca se cruza (posicion sin SL o sin liquidacion)."""
    return -math.inf if is_long else math.inf


def _crosses(is_long: bool, price: float, threshold: float) -> bool:
    return price <= threshold if is_long else price >= threshold


class PositionMonitor:
    def __init__(
        self,
        db: Database,
        rest_client: BitunixRestClient,
        paper_backend: PaperBackend,
        settings: Settings,
    ) -> None:
        self.db = db
        self.rest = rest_client
        self.paper = paper_backend
        self.settings = settings
        self.marks: dict[str, float] = {}
        self.ticks_received = 0
        self.liquidation_confirmations = 0
        self._trades: dict[int, Trade] = {}
        self._dirty: set[int] = set()
        self._last_tick_at: dict[str, float] = {}
        self._tick_alerted: set[str] = set()
        self._feed_last_at: float | None = None
        self._feed_persist_at = 0.0
        self._feed_stale = False
        self._funding_refresh_at: dict[str, float] = {}
        self._liq_checked_at: dict[int, float] = {}
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    # --- latido del feed (ws_feed) ------------------------------------------

    async def start_feed(self) -> None:
        """Registra el feed al arrancar: sin esta fila el riesgo no comprueba el latido."""
        await health_repo.record_success(self.db, health_repo.WS_FEED_SOURCE, datetime.now(UTC))
        self._feed_last_at = time.monotonic()
        self._feed_persist_at = self._feed_last_at

    async def handle_ws_message(self, message: dict) -> None:
        """Cualquier mensaje del servidor (pong incluido) es latido de conexion; los
        `tickers` alimentan la evaluacion de posiciones."""
        await self._note_feed_activity()
        if message.get("ch") != "tickers":
            return
        data = message.get("data")
        if isinstance(data, dict):
            items = [data]
        elif isinstance(data, list):
            items = data
        else:
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = item.get("s")
            last = item.get("la")
            if not symbol or last is None:
                continue
            try:
                price = float(last)
            except (TypeError, ValueError):
                continue
            if price > 0:
                await self.on_tick(symbol, price)

    async def _note_feed_activity(self) -> None:
        now = time.monotonic()
        self._feed_last_at = now
        if now - self._feed_persist_at >= FEED_PERSIST_SECONDS:
            self._feed_persist_at = now
            await health_repo.record_success(
                self.db, health_repo.WS_FEED_SOURCE, datetime.now(UTC)
            )

    # --- evaluacion en vivo (modo tick) -------------------------------------

    async def on_tick(self, symbol: str, price: float) -> None:
        self.ticks_received += 1
        self._last_tick_at[symbol] = time.monotonic()
        self._tick_alerted.discard(symbol)
        for trade in [t for t in self._trades.values() if t.symbol == symbol]:
            await self._evaluate(trade, price)

    async def _evaluate(self, trade: Trade, price: float) -> None:
        is_long = trade.side == Side.LONG
        unbounded = _unbounded(is_long)
        sl_thr = trade.effective_stop if trade.effective_stop is not None else unbounded
        liq_thr = trade.liq_price if trade.liq_price is not None else unbounded

        candidates: list[tuple[str, float]] = []
        sl_hit = check_adverse_tick(is_long, price, price, sl_thr, unbounded)
        if sl_hit is not None:
            candidates.append(("SL", price))
        if _crosses(is_long, price, liq_thr):
            mark = await self._confirm_liquidation(trade, liq_thr)
            if mark is not None:
                candidates.append(("LIQUIDATION", mark))
        if candidates:
            order = [name for name, _ in order_adverse_thresholds(is_long, sl_thr, liq_thr)]
            name, exit_price = min(candidates, key=lambda c: order.index(c[0]))
            await self._close(trade, exit_price, self._adverse_reason(trade, name), FILL_TICK)
            return

        if trade.tp_price is not None and check_favorable_tp_tick(
            is_long, price, trade.tp_price
        ) is not None:
            fill = tp_tick_fill_price(
                is_long, price, trade.tp_price, self.settings.tp_gap_tolerance_pct
            )
            await self._close(trade, fill, "TP", FILL_TICK)
            return

        if trade.trailing_distance is not None and trade.effective_stop is not None:
            best0 = trade.best_price if trade.best_price is not None else trade.entry_price
            best, eff = advance_trailing_stop(
                is_long, best0, trade.effective_stop, trade.trailing_distance, price
            )
            if best != trade.best_price or eff != trade.effective_stop:
                trade.best_price = best
                trade.effective_stop = eff
                self._dirty.add(trade.id)

    @staticmethod
    def _adverse_reason(trade: Trade, name: str) -> str:
        if name == "LIQUIDATION":
            return "LIQUIDATION"
        return "SL" if trade.effective_stop == trade.sl_price else "TRAILING"

    async def _confirm_liquidation(self, trade: Trade, liq_thr: float) -> float | None:
        """El ultimo precio cruzo la liquidacion: el mark por REST debe confirmarlo.
        Sin confirmacion (o si la consulta falla) no se liquida, y se reintenta como
        mucho cada `LIQ_CONFIRM_THROTTLE_SECONDS`."""
        now = time.monotonic()
        last = self._liq_checked_at.get(trade.id)
        if last is not None and now - last < LIQ_CONFIRM_THROTTLE_SECONDS:
            return None
        self._liq_checked_at[trade.id] = now
        try:
            ticker = await self._fetch_rest_ticker(trade.symbol)
        except Exception as exc:  # noqa: BLE001 - sin confirmacion no se liquida
            logger.warning("No se pudo confirmar liquidacion de %s por REST: %s",
                           trade.symbol, exc)
            return None
        self.marks[trade.symbol] = ticker["mark"]
        if _crosses(trade.side == Side.LONG, ticker["mark"], liq_thr):
            self.liquidation_confirmations += 1
            return ticker["mark"]
        return None

    async def _close(
        self, trade: Trade, price: float, reason: str, fill_source: str,
        closed_at: datetime | None = None,
    ) -> None:
        closed = await self.paper.close_if_open(
            trade.id, price, reason, closed_at=closed_at, fill_source=fill_source
        )
        self._trades.pop(trade.id, None)
        self._dirty.discard(trade.id)
        self._liq_checked_at.pop(trade.id, None)
        if closed is not None:
            logger.info("Monitor cerro trade=%d %s motivo=%s precio=%.4f fuente=%s",
                        trade.id, trade.symbol, reason, price, fill_source)

    async def _fetch_rest_ticker(self, symbol: str) -> dict[str, float]:
        tickers = await self.rest.get_tickers(symbol)
        ticker = next((t for t in tickers if t.get("symbol") == symbol), None)
        if ticker is None:
            raise ValueError(f"Sin ticker REST para {symbol}")
        last = float(ticker["lastPrice"])
        return {"last": last, "mark": float(ticker.get("markPrice") or last)}

    # --- ciclo periodico ----------------------------------------------------

    async def tick_periodic(self, now_ms: int | None = None) -> None:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        await self._flush_dirty()
        await self._reload_trades()
        await self._check_tick_ages()
        await self._refresh_marks()
        await self._accrue_funding_all(now_ms)
        await self._track_equity()
        await system_state_repo.set_state(self.db, MONITOR_LAST_SEEN_KEY, str(now_ms))
        self._check_feed_transition()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick_periodic()
            except Exception:  # noqa: BLE001 - un ciclo fallido no tumba el monitor
                logger.exception("Error en el ciclo periodico del monitor")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.monitor_tick_seconds
                )
            except TimeoutError:
                pass

    async def shutdown(self) -> None:
        await self._flush_dirty()
        await system_state_repo.set_state(
            self.db, MONITOR_LAST_SEEN_KEY, str(int(time.time() * 1000))
        )

    async def _flush_dirty(self) -> None:
        for trade_id in list(self._dirty):
            trade = self._trades.get(trade_id)
            if trade is not None:
                await trades_repo.update_risk_state(
                    self.db, trade_id, trade.effective_stop, trade.best_price
                )
        self._dirty.clear()

    async def _reload_trades(self) -> None:
        fresh: dict[int, Trade] = {}
        for trade in await trades_repo.get_open_positions(self.db):
            if trade.id in self._trades:
                fresh[trade.id] = self._trades[trade.id]
                continue
            if trade.liq_price is None:
                trade = await self._backfill_liq(trade)
            fresh[trade.id] = trade
        self._trades = fresh

    async def _backfill_liq(self, trade: Trade) -> Trade:
        spec = await specs_repo.get_spec(self.db, trade.symbol)
        liq = compute_liquidation_price(
            trade.side, trade.entry_price, trade.leverage, trade.notional_usdt,
            spec.margin_tiers_json if spec else None,
        )
        await trades_repo.update_liq_price(self.db, trade.id, liq)
        return trade.model_copy(update={"liq_price": liq})

    async def _check_tick_ages(self) -> None:
        now = time.monotonic()
        symbols = {t.symbol for t in self._trades.values()}
        for symbol in symbols:
            last = self._last_tick_at.get(symbol)
            age = math.inf if last is None else now - last
            if age <= self.settings.tick_stale_seconds:
                continue
            if symbol not in self._tick_alerted:
                self._tick_alerted.add(symbol)
                desde = "nunca desde el arranque" if age == math.inf else f"desde hace {age:.0f}s"
                logger.warning(
                    "Sin ticks de %s %s (umbral %.0fs); se consulta el precio por REST",
                    symbol, desde, self.settings.tick_stale_seconds,
                )
            try:
                ticker = await self._fetch_rest_ticker(symbol)
            except Exception as exc:  # noqa: BLE001 - sin precio no se evalua, el cierre sigue vivo
                logger.warning("No se pudo consultar precio REST de %s: %s", symbol, exc)
                continue
            for trade in [t for t in self._trades.values() if t.symbol == symbol]:
                await self._evaluate(trade, ticker["last"])

    async def _refresh_marks(self) -> None:
        for symbol in {t.symbol for t in self._trades.values()}:
            try:
                ticker = await self._fetch_rest_ticker(symbol)
            except Exception as exc:  # noqa: BLE001 - la marca anterior se conserva
                logger.warning("No se pudo refrescar el mark de %s: %s", symbol, exc)
                continue
            self.marks[symbol] = ticker["mark"]

    async def _accrue_funding_all(self, now_ms: int) -> None:
        for trade in list(self._trades.values()):
            await self._refresh_funding(trade.symbol)
            self._trades[trade.id] = await accrue_funding(self.db, trade, now_ms)

    async def _refresh_funding(self, symbol: str) -> None:
        """Repone la cola de funding por la API publica, como mucho cada
        `FUNDING_REFRESH_SECONDS`. Solo descarga si el ultimo evento supera el
        intervalo del contrato mas el margen."""
        now = time.monotonic()
        last = self._funding_refresh_at.get(symbol)
        if last is not None and now - last < FUNDING_REFRESH_SECONDS:
            return
        self._funding_refresh_at[symbol] = now
        spec = await specs_repo.get_spec(self.db, symbol)
        interval_h = (
            float(spec.funding_interval_hours)
            if spec and spec.funding_interval_hours
            else 8.0
        )
        tolerance_ms = int((interval_h + self.settings.funding_stale_margin_hours) * 3_600_000)
        now_ms = int(time.time() * 1000)
        opened_ms = [int(t.opened_at.timestamp() * 1000)
                     for t in self._trades.values() if t.symbol == symbol]
        start_ms = min(opened_ms) - 3_600_000 if opened_ms else now_ms
        try:
            await download_missing_funding(
                self.rest, self.db, symbol, start_ms, now_ms, freshness_tolerance_ms=tolerance_ms,
            )
        except Exception as exc:  # noqa: BLE001 - se acumula con la cache que haya
            logger.warning("No se pudo refrescar funding de %s: %s", symbol, exc)

    async def _track_equity(self) -> None:
        equity = await self.paper.get_equity(marks=self.marks)
        await risk_engine.update_equity_tracking(self.db, self.settings, equity)

    def _check_feed_transition(self) -> None:
        stale = (
            self._feed_last_at is None
            or time.monotonic() - self._feed_last_at > self.settings.ws_stale_after_seconds
        )
        if stale and not self._feed_stale:
            logger.warning("Feed WS sin latido; no se abren entradas nuevas (cierres sin cambios)")
        elif not stale and self._feed_stale:
            logger.info("Feed WS recuperado")
        self._feed_stale = stale

    # --- reconciliacion al reiniciar (modo vela, velas 1m) -------------------

    async def reconcile_on_startup(self, now_ms: int | None = None) -> list[tuple[int, str]]:
        """Reproduce con velas 1m el periodo en que el proceso estuvo caido, para cada
        posicion abierta: SL, TP, trailing, liquidacion y funding que habrian ocurrido.
        Si no se pueden obtener velas completas, la posicion queda abierta y los ticks
        en vivo toman el control (error registrado como CRITICAL)."""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        raw = await system_state_repo.get_state(self.db, MONITOR_LAST_SEEN_KEY)
        last_seen_ms = int(raw) if raw else None
        await self._reload_trades()
        outcomes: list[tuple[int, str]] = []
        for trade in list(self._trades.values()):
            try:
                outcome = await self._reconcile_trade(trade, last_seen_ms, now_ms)
            except Exception:  # noqa: BLE001 - una posicion no debe impedir arrancar
                logger.critical(
                    "Reconciliacion fallida para trade=%d %s; queda abierta y los ticks en vivo "
                    "toman el control", trade.id, trade.symbol, exc_info=True,
                )
                outcome = "ERROR"
            outcomes.append((trade.id, outcome))
        return outcomes

    async def _reconcile_trade(
        self, trade: Trade, last_seen_ms: int | None, now_ms: int
    ) -> str:
        opened_ms = int(trade.opened_at.timestamp() * 1000)
        start_ms = max(opened_ms, last_seen_ms or opened_ms)
        if now_ms - start_ms < RECONCILE_MIN_WINDOW_MS:
            return "SIN_VENTANA"

        last_bars, mark_bars = await self._load_1m_bars(trade.symbol, start_ms, now_ms)
        is_long = trade.side == Side.LONG
        marks_by_open = {b.open_time: b for b in mark_bars}
        current = trade
        for bar in last_bars:
            bar_end = bar.open_time + BAR_MS
            if bar_end <= start_ms:
                continue
            current = await accrue_funding(self.db, current, bar_end)
            mark_bar = marks_by_open.get(bar.open_time, bar)
            unbounded = _unbounded(is_long)
            sl_thr = current.effective_stop if current.effective_stop is not None else unbounded
            liq_thr = current.liq_price if current.liq_price is not None else unbounded
            closed_at = datetime.fromtimestamp(bar_end / 1000, tz=UTC)

            adverse = check_adverse_bar(
                is_long, bar.open, bar.low, bar.high, mark_bar.low, mark_bar.high,
                sl_thr, liq_thr,
            )
            if adverse is not None:
                name, exit_price = adverse
                await self._close(
                    current, exit_price, self._adverse_reason(current, name), FILL_CANDLE,
                    closed_at=closed_at,
                )
                return f"CERRADA_{self._adverse_reason(current, name)}"

            if current.tp_price is not None:
                tp_exit = check_favorable_tp_bar(
                    is_long, bar.open, bar.high, bar.low, current.tp_price
                )
                if tp_exit is not None:
                    await self._close(current, tp_exit, "TP", FILL_CANDLE, closed_at=closed_at)
                    return "CERRADA_TP"

            if current.trailing_distance is not None and current.effective_stop is not None:
                best0 = (
                    current.best_price if current.best_price is not None
                    else current.entry_price
                )
                extreme = bar.high if is_long else bar.low
                best, eff = advance_trailing_stop(
                    is_long, best0, current.effective_stop, current.trailing_distance, extreme
                )
                if best != current.best_price or eff != current.effective_stop:
                    await trades_repo.update_risk_state(self.db, current.id, eff, best)
                    current = current.model_copy(update={"best_price": best, "effective_stop": eff})

        current = await accrue_funding(self.db, current, now_ms)
        self._trades[trade.id] = current
        return "ABIERTA"

    async def _load_1m_bars(self, symbol: str, start_ms: int, now_ms: int):
        for price_type in ("LAST_PRICE", "MARK_PRICE"):
            await download_missing(
                self.rest, self.db, symbol, "1m", start_ms, now_ms, price_type=price_type
            )
        first_open = (start_ms // BAR_MS) * BAR_MS
        last_closed_open = (now_ms // BAR_MS) * BAR_MS - BAR_MS
        expected = (last_closed_open - first_open) // BAR_MS + 1
        last = await ohlcv_repo.get_bars(
            self.db, symbol, "1m", "LAST_PRICE", first_open, last_closed_open
        )
        mark = await ohlcv_repo.get_bars(
            self.db, symbol, "1m", "MARK_PRICE", first_open, last_closed_open
        )
        if len(last) < expected - RECONCILE_MAX_MISSING_BARS:
            raise RuntimeError(
                f"velas 1m LAST_PRICE incompletas para {symbol}: {len(last)} de {expected}"
            )
        return last, mark
