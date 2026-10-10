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
from app.market.ohlcv_history import download_missing, find_gaps
from app.persistence.database import Database
from app.persistence.models import OHLCVBar, ShadowTrade, Side, Trade
from app.persistence.repositories import (
    health_repo,
    ohlcv_repo,
    shadow_repo,
    specs_repo,
    system_state_repo,
    trades_repo,
)
from app.trading import risk_engine
from app.trading.funding_accrual import accrue_funding
from app.trading.shadow_book import ShadowBook
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
TICKER_CACHE_SECONDS = 2.0
MONITOR_LAST_SEEN_KEY = "monitor_last_seen_ms"
BAR_MS = 60_000
RECONCILE_MIN_WINDOW_MS = BAR_MS
# Incidente de estabilidad 2026-10-10, Etapa 2a (docs/FASE2_INTEGRIDAD_VELAS.md):
# tras `fill_gaps`, solo se tolera un hueco residual de LAST_PRICE si es AISLADO
# (ningun minuto vecino falta tambien), su minuto existe en MARK_PRICE (se
# sustituye esa vela para evaluar SL/TP/liquidacion) y el total de huecos
# tolerados no pasa de este tope. Cualquier otro caso sigue en CRITICAL.
RECONCILE_GAP_TOLERANCE_FRACTION = 0.001
RECONCILE_GAP_TOLERANCE_MIN = 2
# Reintentos de una reconciliacion que fallo (posicion marcada, no cerrada a
# ciegas por tick -- Etapa 2c). Tras agotarlos, una SOMBRA se cierra sin PnL
# (RECONCILE_FAILED); una posicion REAL sigue vigilada en vivo indefinidamente,
# solo marcada (ver `FILL_TICK_UNRECONCILED` y `open_real_positions`).
MAX_SHADOW_RECONCILE_ATTEMPTS = 3
RECONCILE_RETRY_SECONDS = 300.0
# Revision de Etapa 2, correccion 2: 3 intentos cada 300s son ~10 min -- un
# corte de internet o de la exchange de esa duracion descartaria sombras
# sanas. Rendirse exige ADEMAS un minimo de horas de racha fallida continua
# (no solo de intentos): las dos condiciones deben cumplirse juntas.
MIN_SHADOW_RECONCILE_GIVE_UP_HOURS = 6.0

FILL_TICK = "TICK"
FILL_CANDLE = "CANDLE_RECON"
FILL_CANDLE_MARK_SUBSTITUTE = "CANDLE_RECON_MARK_SUBSTITUTE"
FILL_REST = "REST_MARK"
# Cierre por tick de una posicion REAL cuya ultima reconciliacion esta marcada
# como fallida: mismo precio/regla que FILL_TICK, pero auditable por separado
# (la "vela" real que deberia haber evaluado el SL/TP no se pudo reconstruir).
FILL_TICK_UNRECONCILED = "TICK_UNRECONCILED"


def _unbounded(is_long: bool) -> float:
    """Umbral que nunca se cruza (posicion sin SL o sin liquidacion)."""
    return -math.inf if is_long else math.inf


def _crosses(is_long: bool, price: float, threshold: float) -> bool:
    return price <= threshold if is_long else price >= threshold


def _residual_gaps_are_tolerable(gaps: list[int], mark_times: set[int], expected: int) -> bool:
    """Decide si los huecos de LAST_PRICE que quedaron tras `fill_gaps` se pueden
    sustituir por MARK_PRICE en vez de marcar la reconciliacion como fallida
    (Etapa 2a). Exige TODO lo siguiente:
    - el total no supera `max(RECONCILE_GAP_TOLERANCE_MIN, 0.1% de la ventana)`;
    - cada hueco es AISLADO (ningun minuto vecino falta tambien, ida o vuelta);
    - cada hueco existe en MARK_PRICE (si no, no hay con que sustituirlo)."""
    if not gaps:
        return True
    cap = max(RECONCILE_GAP_TOLERANCE_MIN, math.ceil(expected * RECONCILE_GAP_TOLERANCE_FRACTION))
    if len(gaps) > cap:
        return False
    gap_set = set(gaps)
    for g in gaps:
        if g not in mark_times:
            return False
        if (g - BAR_MS) in gap_set or (g + BAR_MS) in gap_set:
            return False
    return True


class PositionMonitor:
    def __init__(
        self,
        db: Database,
        rest_client: BitunixRestClient,
        paper_backend: PaperBackend,
        settings: Settings,
        shadow: ShadowBook | None = None,
    ) -> None:
        self.db = db
        self.rest = rest_client
        self.shadow = shadow
        self.paper = paper_backend
        self.settings = settings
        self.marks: dict[str, float] = {}
        self.ticks_received = 0
        self.liquidation_confirmations = 0
        # Clave (es_sombra, id): las posiciones reales y las sombra tienen ids independientes.
        self._trades: dict[tuple[bool, int], Trade] = {}
        self._dirty: set[tuple[bool, int]] = set()
        self._last_tick_at: dict[str, float] = {}
        self._tick_alerted: set[str] = set()
        self._feed_last_at: float | None = None
        self._feed_persist_at = 0.0
        self._feed_stale = False
        self._funding_refresh_at: dict[str, float] = {}
        self._liq_checked_at: dict[tuple[bool, int], float] = {}
        self._ticker_cache: dict[str, tuple[float, dict[str, float]]] = {}
        self._reconcile_retry_at: dict[tuple[bool, int], float] = {}
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
        if isinstance(trade, ShadowTrade) and trade.reconciliation_failed:
            # Etapa 2c: una sombra con reconciliacion fallida queda CONGELADA (no se
            # cierra con un precio en vivo que no es fiable) hasta que un reintento la
            # reconcilie o se agoten los intentos (ver `_retry_failed_reconciliations`).
            return
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
                self._dirty.add(self._key(trade))

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
        last = self._liq_checked_at.get(self._key(trade))
        if last is not None and now - last < LIQ_CONFIRM_THROTTLE_SECONDS:
            return None
        self._liq_checked_at[self._key(trade)] = now
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
        if fill_source == FILL_TICK and trade.reconciliation_failed:
            # Etapa 2c: el cierre por tick de una posicion REAL marcada queda
            # distinguible de uno normal -- el "tick" real es una foto REST de un
            # periodo que no se pudo reconstruir con velas, no un feed continuo.
            fill_source = FILL_TICK_UNRECONCILED
        if isinstance(trade, ShadowTrade):
            closed = await self.shadow.close(
                trade, price, reason, closed_at=closed_at, fill_source=fill_source
            )
        else:
            closed = await self.paper.close_if_open(
                trade.id, price, reason, closed_at=closed_at, fill_source=fill_source
            )
        key = self._key(trade)
        self._trades.pop(key, None)
        self._dirty.discard(key)
        self._liq_checked_at.pop(key, None)
        if closed is not None:
            logger.info("Monitor cerro trade=%d %s motivo=%s precio=%.4f fuente=%s",
                        trade.id, trade.symbol, reason, price, fill_source)

    async def _fetch_rest_ticker(self, symbol: str) -> dict[str, float]:
        """Unica consulta REST de ticker del monitor (marcas, antiguedad de ticks y
        confirmacion de liquidacion). Reutiliza la respuesta de los ultimos
        `TICKER_CACHE_SECONDS` para no pedir el mismo simbolo dos veces en un mismo ciclo."""
        now = time.monotonic()
        cached = self._ticker_cache.get(symbol)
        if cached is not None and now - cached[0] < TICKER_CACHE_SECONDS:
            return cached[1]
        tickers = await self.rest.get_tickers(symbol)
        ticker = next((t for t in tickers if t.get("symbol") == symbol), None)
        if ticker is None:
            raise ValueError(f"Sin ticker REST para {symbol}")
        last = float(ticker["lastPrice"])
        result = {"last": last, "mark": float(ticker.get("markPrice") or last)}
        self._ticker_cache[symbol] = (now, result)
        return result

    # --- ciclo periodico ----------------------------------------------------

    async def tick_periodic(self, now_ms: int | None = None) -> None:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        await self._flush_dirty()
        await self._reload_trades()
        await self._retry_failed_reconciliations(now_ms)
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

    @staticmethod
    def _key(trade: Trade) -> tuple[bool, int]:
        return (isinstance(trade, ShadowTrade), trade.id)

    async def _persist_risk(self, trade: Trade) -> None:
        if isinstance(trade, ShadowTrade):
            await self.shadow.update_risk(trade)
        else:
            await trades_repo.update_risk_state(
                self.db, trade.id, trade.effective_stop, trade.best_price
            )

    async def _accrue(self, trade: Trade, until_ms: int) -> Trade:
        if isinstance(trade, ShadowTrade):
            return await self.shadow.accrue_funding(trade, until_ms)
        return await accrue_funding(self.db, trade, until_ms)

    async def _flush_dirty(self) -> None:
        for key in list(self._dirty):
            trade = self._trades.get(key)
            if trade is not None:
                await self._persist_risk(trade)
        self._dirty.clear()

    async def _reload_trades(self) -> None:
        fresh: dict[tuple[bool, int], Trade] = {}
        for trade in await trades_repo.get_open_positions(self.db):
            key = (False, trade.id)
            if key in self._trades:
                fresh[key] = self._trades[key]
                continue
            if trade.liq_price is None:
                trade = await self._backfill_liq(trade)
            fresh[key] = trade
        if self.shadow is not None:
            for trade in await self.shadow.load_open():
                key = (True, trade.id)
                fresh[key] = self._trades.get(key, trade)
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
        for key, trade in list(self._trades.items()):
            await self._refresh_funding(trade.symbol)
            self._trades[key] = await self._accrue(trade, now_ms)

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
        Si no se pueden obtener velas completas, la posicion queda MARCADA (Etapa 2c:
        `reconciliation_failed`) en vez de cerrarse a ciegas; los ticks en vivo toman
        el control mientras tanto (sombras: congeladas, ver `_evaluate`) y se reintenta
        periodicamente (`_retry_failed_reconciliations`)."""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        raw = await system_state_repo.get_state(self.db, MONITOR_LAST_SEEN_KEY)
        last_seen_ms = int(raw) if raw else None
        await self._reload_trades()
        outcomes: list[tuple[int, str]] = []
        for trade in list(self._trades.values()):
            # Correccion 1 (revision de Etapa 2): si YA esta marcada de una racha
            # anterior, se reconcilia desde donde quedo esa racha
            # (`reconciliation_window_start_ms`), no desde el `last_seen_ms`
            # global actual -- que para esta posicion ya avanzo de mas.
            effective_last_seen = (
                trade.reconciliation_window_start_ms
                if trade.reconciliation_failed else last_seen_ms
            )
            try:
                outcome = await self._reconcile_trade(trade, effective_last_seen, now_ms)
            except Exception:  # noqa: BLE001 - una posicion no debe impedir arrancar
                logger.critical(
                    "Reconciliacion fallida para trade=%d %s; se marca y se reintenta "
                    "(cada %.0fs, minimo %d intentos y %.0fh de racha si es sombra)",
                    trade.id, trade.symbol, RECONCILE_RETRY_SECONDS,
                    MAX_SHADOW_RECONCILE_ATTEMPTS, MIN_SHADOW_RECONCILE_GIVE_UP_HOURS,
                    exc_info=True,
                )
                await self._mark_reconcile_attempt_failed(trade, now_ms, effective_last_seen)
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

        last_bars, mark_bars, substituted = await self._load_1m_bars(
            trade.symbol, start_ms, now_ms
        )
        is_long = trade.side == Side.LONG
        marks_by_open = {b.open_time: b for b in mark_bars}
        current = trade
        for bar in last_bars:
            bar_end = bar.open_time + BAR_MS
            if bar_end <= start_ms:
                continue
            current = await self._accrue(current, bar_end)
            if bar.open_time < opened_ms:
                # Vela que contiene la apertura: su mecha puede ser anterior a la
                # posicion. No se evalua para precio (riesgo de cierre espurio de
                # como mucho un minuto); el funding ya se aplico arriba por tiempo.
                continue
            mark_bar = marks_by_open.get(bar.open_time, bar)
            fill_source = (
                FILL_CANDLE_MARK_SUBSTITUTE if bar.open_time in substituted else FILL_CANDLE
            )
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
                    current, exit_price, self._adverse_reason(current, name), fill_source,
                    closed_at=closed_at,
                )
                return f"CERRADA_{self._adverse_reason(current, name)}"

            if current.tp_price is not None:
                tp_exit = check_favorable_tp_bar(
                    is_long, bar.open, bar.high, bar.low, current.tp_price
                )
                if tp_exit is not None:
                    await self._close(current, tp_exit, "TP", fill_source, closed_at=closed_at)
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
                    current = current.model_copy(update={"best_price": best, "effective_stop": eff})
                    await self._persist_risk(current)

        current = await self._accrue(current, now_ms)
        self._trades[self._key(trade)] = current
        return "ABIERTA"

    async def _load_1m_bars(
        self, symbol: str, start_ms: int, now_ms: int
    ) -> tuple[list[OHLCVBar], list[OHLCVBar], set[int]]:
        """Devuelve `(last_bars, mark_bars, substituted_times)`: `substituted_times`
        son los `open_time` de `last_bars` cuya vela en realidad viene de MARK_PRICE
        (Etapa 2a, hueco residual tolerado) -- `_reconcile_trade` los cierra con
        `FILL_CANDLE_MARK_SUBSTITUTE` en vez de `FILL_CANDLE`."""
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
        if len(last) >= expected:
            return last, mark, set()

        gaps = find_gaps([b.open_time for b in last], BAR_MS, first_open, last_closed_open)
        mark_by_open = {b.open_time: b for b in mark}
        if not _residual_gaps_are_tolerable(gaps, set(mark_by_open), expected):
            raise RuntimeError(
                f"velas 1m LAST_PRICE incompletas para {symbol}: {len(last)} de {expected} "
                f"({len(gaps)} hueco(s) fuera de tolerancia)"
            )

        logger.warning(
            "%s: %d vela(s) 1m LAST_PRICE faltante(s) dentro de tolerancia para reconciliar, "
            "sustituida(s) por MARK_PRICE (fill_source=%s): %s",
            symbol, len(gaps), FILL_CANDLE_MARK_SUBSTITUTE,
            ", ".join(datetime.fromtimestamp(g / 1000, tz=UTC).isoformat() for g in gaps),
        )
        merged = sorted([*last, *(mark_by_open[g] for g in gaps)], key=lambda b: b.open_time)
        return merged, mark, set(gaps)

    # --- reintentos de reconciliacion fallida (Etapa 2c) ---------------------

    async def _retry_failed_reconciliations(self, now_ms: int) -> None:
        """Reintenta, a lo sumo cada `RECONCILE_RETRY_SECONDS` por posicion, la
        reconciliacion de toda posicion marcada `reconciliation_failed`. Se llama en
        cada ciclo periodico, ademas de al arrancar (`reconcile_on_startup`)."""
        now_mono = time.monotonic()
        for key, trade in list(self._trades.items()):
            if not trade.reconciliation_failed:
                continue
            last_retry = self._reconcile_retry_at.get(key)
            if last_retry is not None and now_mono - last_retry < RECONCILE_RETRY_SECONDS:
                continue
            self._reconcile_retry_at[key] = now_mono
            await self._attempt_reconcile_retry(trade, now_ms)

    async def _attempt_reconcile_retry(self, trade: Trade, now_ms: int) -> None:
        is_shadow = isinstance(trade, ShadowTrade)
        # Correccion 1: se reintenta desde la MISMA ventana que fallo la primera
        # vez (`reconciliation_window_start_ms`), nunca desde `opened_at`.
        last_seen_ms = trade.reconciliation_window_start_ms
        try:
            outcome = await self._reconcile_trade(trade, last_seen_ms, now_ms)
        except Exception:  # noqa: BLE001 - un reintento fallido no debe tumbar el ciclo
            logger.warning(
                "Reintento de reconciliacion fallido para trade=%d %s",
                trade.id, trade.symbol, exc_info=True,
            )
            await self._mark_reconcile_attempt_failed(trade, now_ms, last_seen_ms)
            return
        logger.info(
            "Reintento de reconciliacion para trade=%d %s: %s", trade.id, trade.symbol, outcome,
        )
        await self._clear_reconciliation_failed(trade.id, is_shadow)
        key = (is_shadow, trade.id)
        current = self._trades.get(key)
        if current is not None:
            self._trades[key] = current.model_copy(update={
                "reconciliation_failed": False, "reconciliation_attempts": 0,
                "reconciliation_window_start_ms": None, "reconciliation_first_failed_at_ms": None,
            })

    async def _mark_reconcile_attempt_failed(
        self, trade: Trade, now_ms: int, last_seen_ms: int | None,
    ) -> None:
        """Marca el intento fallido (posicion marcada, no cerrada a ciegas).

        Correccion 1 (revision de Etapa 2): persiste, SOLO en la primera falla de
        la racha, desde donde reintentar (`reconciliation_window_start_ms`) y
        cuando empezo la racha (`reconciliation_first_failed_at_ms`) -- todo
        reintento posterior (fallido o no) sigue reconciliando desde ESE mismo
        instante, nunca desde `opened_at`: rejugar toda la vida de la posicion con
        el `effective_stop`/`best_price` YA avanzados por trailing contra velas de
        antes de la falla puede cerrarla por error (vela vieja, stop de hoy).

        Correccion 2: una SOMBRA solo se cierra sin PnL cuando agota
        `MAX_SHADOW_RECONCILE_ATTEMPTS` intentos Y lleva al menos
        `MIN_SHADOW_RECONCILE_GIVE_UP_HOURS` de racha fallida continua -- las dos
        condiciones a la vez, para que un corte de red o de la exchange de unos
        minutos no descarte una sombra sana. Una posicion REAL nunca se cierra
        sola: sigue vigilada en vivo indefinidamente, solo marcada."""
        is_shadow = isinstance(trade, ShadowTrade)
        opened_ms = int(trade.opened_at.timestamp() * 1000)
        attempts = trade.reconciliation_attempts + 1
        if trade.reconciliation_failed and trade.reconciliation_window_start_ms is not None:
            window_start_ms = trade.reconciliation_window_start_ms
            first_failed_at_ms = trade.reconciliation_first_failed_at_ms or now_ms
        else:
            window_start_ms = max(opened_ms, last_seen_ms or opened_ms)
            first_failed_at_ms = now_ms

        repo = shadow_repo if is_shadow else trades_repo
        await repo.update_reconciliation_state(
            self.db, trade.id, True, attempts, window_start_ms, first_failed_at_ms,
        )
        updated = trade.model_copy(update={
            "reconciliation_failed": True,
            "reconciliation_attempts": attempts,
            "reconciliation_window_start_ms": window_start_ms,
            "reconciliation_first_failed_at_ms": first_failed_at_ms,
        })
        elapsed_hours = (now_ms - first_failed_at_ms) / 3_600_000
        if (
            is_shadow
            and attempts >= MAX_SHADOW_RECONCILE_ATTEMPTS
            and elapsed_hours >= MIN_SHADOW_RECONCILE_GIVE_UP_HOURS
        ):
            await self._give_up_shadow_reconciliation(updated, elapsed_hours)
            return
        self._trades[self._key(updated)] = updated

    async def _give_up_shadow_reconciliation(
        self, trade: ShadowTrade, elapsed_hours: float
    ) -> None:
        await self.shadow.close_unreliable(trade)
        key = self._key(trade)
        self._trades.pop(key, None)
        self._reconcile_retry_at.pop(key, None)
        logger.warning(
            "Sombra trade=%d %s: reconciliacion fallo %d veces seguidas en %.1fh, se cierra "
            "sin PnL (RECONCILE_FAILED, excluida de ai_value)",
            trade.id, trade.symbol, trade.reconciliation_attempts, elapsed_hours,
        )

    async def _clear_reconciliation_failed(self, trade_id: int, is_shadow: bool) -> None:
        repo = shadow_repo if is_shadow else trades_repo
        await repo.update_reconciliation_state(self.db, trade_id, False, 0, None, None)
