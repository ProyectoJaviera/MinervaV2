"""Motor de backtest event-driven, una posicion a la vez por (estrategia,
simbolo, timeframe) -- docs/FASE2_PLAN.md seccion A.

Reglas implementadas (ver el plan para la justificacion de cada una):
- Sin sesgo de anticipacion: senal evaluada con la vela `i` cerrada, se
  llena en el OPEN de la vela `i+1` (sin desplazamiento de precio -- el
  slippage se modela como un costo plano en USDT, no como un cambio de
  precio, para que el stress-test de fees/slippage sea una simple
  recomputacion posterior sin re-simular, ver `metrics.py`).
- Liquidacion evaluada con MARK_PRICE; SL/TP con LAST_PRICE.
- Peor caso en la misma vela: primero el movimiento ADVERSO (liquidacion,
  luego SL/trailing), despues el FAVORABLE (TP, actualizacion de trailing).
  El trailing actualizado en una vela solo puede disparar en una vela
  POSTERIOR (nunca en la misma vela en que se actualizo).
- Funding prorrateado por vela (ver `funding.py`).
- Tope de riesgo por operacion: si el SL implicaria perder mas de
  `MAX_SL_MARGIN_LOSS_PCT` del margen (a leverage configurado), la entrada
  se omite y se registra en `skipped`.
- Descarta la ultima vela si todavia no cerro (`ohlcv_history.drop_incomplete_last_bar`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from app.backtesting.funding import build_funding_series, funding_cost_for_bar
from app.backtesting.liquidation import compute_liquidation_price
from app.config import Settings
from app.core.logging import get_logger
from app.execution.pnl import compute_close_result, compute_open_fill
from app.market.bitunix_rest import BitunixRestClient
from app.market.ohlcv_history import drop_incomplete_last_bar, get_or_fetch, interval_to_ms
from app.persistence.database import Database
from app.persistence.models import Side
from app.persistence.repositories import funding_repo, ohlcv_repo, specs_repo
from app.strategies.base import BaseStrategy, Signal

logger = get_logger(__name__)

# Ventana maxima de velas que se le pasa a una estrategia para calcular
# indicadores/senales. Sin este tope, pasarle `df.iloc[:i]` (todo el
# historial visto hasta ahora) hace que cada vela recalcule EMA/RSI/ATR
# desde el inicio del dataset -> O(n^2) total (medido: 46s para un solo
# simbolo/timeframe/estrategia en ~9760 velas de 4h; habria escalado a
# horas con 1h). Los indicadores usados (EMA/RSI/ATR/Bollinger/Donchian,
# periodo maximo 50) convergen numericamente dentro de unas pocas veces su
# periodo; 300 velas da un margen amplio sin cambiar el resultado.
MAX_LOOKBACK_BARS = 300


@dataclass
class RawTrade:
    strategy: str
    symbol: str
    timeframe: str
    side: Side
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    qty: float
    margin_usdt: float
    leverage: int
    fee_entry_usdt: float
    fee_exit_usdt: float
    slippage_cost_usdt: float
    funding_paid_usdt: float
    funding_is_approximated: bool
    pnl_gross_usdt: float
    pnl_net_usdt: float
    close_reason: str
    sl_margin_loss_pct: float | None


@dataclass
class RawSkip:
    strategy: str
    symbol: str
    timeframe: str
    side: Side
    ts: datetime
    intended_sl_margin_loss_pct: float


@dataclass
class _Position:
    side: Side
    entry_time_ms: int
    entry_price: float
    qty: float
    margin_usdt: float
    notional: float
    leverage: int
    fee_entry_usdt: float
    sl_price: float
    original_sl_price: float
    tp_price: float | None
    trailing_distance: float | None
    effective_stop: float
    best_price: float
    liq_price: float
    slippage_entry_usdt: float
    sl_margin_loss_pct: float | None
    funding_paid_usdt: float = 0.0
    funding_is_approximated: bool = False


_FUNDING_FRESHNESS_TOLERANCE_MS = 2 * 24 * 60 * 60 * 1000  # 2 dias


async def _fetch_funding_events(
    client: BitunixRestClient, db: Database, symbol: str, start_time_ms: int, end_time_ms: int
) -> list[tuple[int, float]]:
    """Pagina hacia atras `get_funding_rate_history` (misma logica que
    `ohlcv_history`, verificada empiricamente -- ver docs/FASE2_PLAN.md),
    cacheada en `funding_cache` (mismo motivo que `ohlcv_cache`: evitar
    re-descargar en cada reintento/corrida)."""
    covered = await funding_repo.get_covered_funding_times(db, symbol)
    floor = await ohlcv_repo.get_floor(db, symbol, "__funding__", "__funding__")

    already_covered = False
    if covered:
        min_ok = min(covered) <= start_time_ms or (floor is not None and floor <= min(covered))
        already_covered = min_ok and max(covered) >= end_time_ms - _FUNDING_FRESHNESS_TOLERANCE_MS

    if not already_covered:
        cursor = end_time_ms
        while cursor >= start_time_ms:
            rows = await client.get_funding_rate_history(symbol, end_time=cursor, limit=200)
            if not rows:
                await ohlcv_repo.set_floor(db, symbol, "__funding__", "__funding__", cursor)
                break
            batch = [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
            await funding_repo.upsert_funding(db, symbol, batch)
            oldest = min(t for t, _ in batch)
            if len(rows) < 200:
                await ohlcv_repo.set_floor(db, symbol, "__funding__", "__funding__", oldest)
                break
            if oldest <= start_time_ms:
                break
            cursor = oldest - 1
            # Pausa deliberada entre paginas -- ver la misma nota en
            # app/market/ohlcv_history.py::get_or_fetch.
            await asyncio.sleep(1.0)

    return await funding_repo.get_funding(db, symbol, start_time_ms, end_time_ms)


def _fallback_sl_tp(side: Signal, entry_price: float, settings: Settings) -> tuple[float, float]:
    sl_pct = settings.backtest_fallback_sl_pct
    tp_pct = settings.backtest_fallback_tp_pct
    if side == Signal.LONG:
        return entry_price * (1 - sl_pct), entry_price * (1 + tp_pct)
    return entry_price * (1 + sl_pct), entry_price * (1 - tp_pct)


async def run_backtest(
    rest_client: BitunixRestClient,
    db: Database,
    strategy: BaseStrategy,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    start_time_ms: int,
    end_time_ms: int,
    settings: Settings,
) -> tuple[list[RawTrade], list[RawSkip]]:
    step_ms = interval_to_ms(timeframe)
    if step_ms is None:
        raise ValueError(f"Intervalo desconocido para backtest: {timeframe}")

    last_bars = await get_or_fetch(
        rest_client, db, symbol, timeframe, start_time_ms, end_time_ms, "LAST_PRICE"
    )
    mark_bars = await get_or_fetch(
        rest_client, db, symbol, timeframe, start_time_ms, end_time_ms, "MARK_PRICE"
    )

    now_ms = int(time.time() * 1000)
    last_bars = drop_incomplete_last_bar(last_bars, timeframe, now_ms)
    mark_bars = drop_incomplete_last_bar(mark_bars, timeframe, now_ms)

    mark_by_time = {b.open_time: b for b in mark_bars}
    aligned = [(b, mark_by_time[b.open_time]) for b in last_bars if b.open_time in mark_by_time]
    aligned.sort(key=lambda pair: pair[0].open_time)

    min_bars_required = 60
    if len(aligned) < min_bars_required:
        logger.info("%s %s %s: solo %d velas alineadas, se omite (minimo %d)",
                    strategy_name, symbol, timeframe, len(aligned), min_bars_required)
        return [], []

    open_times = [p[0].open_time for p in aligned]
    opens = [p[0].open for p in aligned]
    highs = [p[0].high for p in aligned]
    lows = [p[0].low for p in aligned]
    closes = [p[0].close for p in aligned]
    mark_highs = [p[1].high for p in aligned]
    mark_lows = [p[1].low for p in aligned]

    funding_events = await _fetch_funding_events(
        rest_client, db, symbol, start_time_ms, end_time_ms
    )
    funding_rates, funding_approx = build_funding_series(open_times, funding_events)

    spec = await specs_repo.get_spec(db, symbol)
    margin_tiers_json = spec.margin_tiers_json if spec else None
    funding_interval_hours = (
        float(spec.funding_interval_hours) if spec and spec.funding_interval_hours else 8.0
    )
    bar_duration_hours = step_ms / 3_600_000

    df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                        "funding_rate": funding_rates})

    margin_usdt = settings.default_margin_usdt
    leverage = settings.leverage
    taker_fee_pct = settings.taker_fee_pct
    slippage_frac = settings.backtest_slippage_bps / 10_000

    trades: list[RawTrade] = []
    skipped: list[RawSkip] = []
    position: _Position | None = None
    pending_signal: Signal | None = None

    def ms_to_dt(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)

    def finalize(exit_price: float, exit_idx: int, reason: str) -> None:
        nonlocal position
        assert position is not None
        result = compute_close_result(
            position.side, position.qty, position.entry_price, exit_price,
            position.fee_entry_usdt, taker_fee_pct,
            extra_costs_usdt=position.slippage_entry_usdt + position.funding_paid_usdt,
        )
        slippage_exit = (position.qty * exit_price) * slippage_frac
        final_net = result.pnl_net_usdt - slippage_exit
        trades.append(RawTrade(
            strategy=strategy_name, symbol=symbol, timeframe=timeframe, side=position.side,
            entry_time=ms_to_dt(position.entry_time_ms), exit_time=ms_to_dt(open_times[exit_idx]),
            entry_price=position.entry_price, exit_price=exit_price, qty=position.qty,
            margin_usdt=position.margin_usdt, leverage=position.leverage,
            fee_entry_usdt=position.fee_entry_usdt, fee_exit_usdt=result.fee_exit_usdt,
            slippage_cost_usdt=position.slippage_entry_usdt + slippage_exit,
            funding_paid_usdt=position.funding_paid_usdt,
            funding_is_approximated=position.funding_is_approximated,
            pnl_gross_usdt=result.pnl_gross_usdt, pnl_net_usdt=final_net,
            close_reason=reason, sl_margin_loss_pct=position.sl_margin_loss_pct,
        ))
        position = None

    for i in range(len(df)):
        if position is not None:
            cost = funding_cost_for_bar(
                position.notional, funding_rates[i], position.side, bar_duration_hours,
                funding_interval_hours,
            )
            position.funding_paid_usdt += cost
            if funding_approx[i]:
                position.funding_is_approximated = True

            is_long = position.side == Side.LONG
            adverse_mark = mark_lows[i] if is_long else mark_highs[i]
            liquidated = (
                adverse_mark <= position.liq_price
                if is_long
                else adverse_mark >= position.liq_price
            )
            if liquidated:
                finalize(position.liq_price, i, "LIQUIDATION")
                continue

            if position is not None:
                adverse_last = lows[i] if is_long else highs[i]
                sl_hit = (
                    adverse_last <= position.effective_stop
                    if is_long
                    else adverse_last >= position.effective_stop
                )
                if sl_hit:
                    is_original_sl = position.effective_stop == position.original_sl_price
                    reason = "SL" if is_original_sl else "TRAILING"
                    finalize(position.effective_stop, i, reason)
                    continue

            if position is not None:
                favorable_last = highs[i] if is_long else lows[i]
                if position.tp_price is not None:
                    tp_hit = (
                        favorable_last >= position.tp_price
                        if is_long
                        else favorable_last <= position.tp_price
                    )
                    if tp_hit:
                        finalize(position.tp_price, i, "TP")
                        continue

            if position is not None and position.trailing_distance is not None:
                favorable_last = highs[i] if is_long else lows[i]
                if is_long:
                    position.best_price = max(position.best_price, favorable_last)
                    candidate = position.best_price - position.trailing_distance
                    position.effective_stop = max(position.effective_stop, candidate)
                else:
                    position.best_price = min(position.best_price, favorable_last)
                    candidate = position.best_price + position.trailing_distance
                    position.effective_stop = min(position.effective_stop, candidate)

        if position is None:
            if pending_signal is not None and i > 0:
                side = pending_signal
                fill_price = opens[i]
                sub_df = df.iloc[max(0, i - MAX_LOOKBACK_BARS) : i]
                fill = compute_open_fill(margin_usdt, leverage, fill_price, taker_fee_pct)

                sl = strategy.stop_price(sub_df, side, fill_price)
                fallback_sl, fallback_tp = _fallback_sl_tp(side, fill_price, settings)
                if sl is None:
                    sl = fallback_sl
                trailing = strategy.trailing_distance(sub_df)
                tp = strategy.take_profit_price(sub_df, side, fill_price)
                if tp is None and trailing is None:
                    tp = fallback_tp

                price_distance = abs(fill_price - sl)
                sl_margin_loss_pct = (price_distance / fill_price) * leverage * 100

                if sl_margin_loss_pct > settings.max_sl_margin_loss_pct:
                    skipped.append(RawSkip(
                        strategy=strategy_name, symbol=symbol, timeframe=timeframe, side=side,
                        ts=ms_to_dt(open_times[i]), intended_sl_margin_loss_pct=sl_margin_loss_pct,
                    ))
                else:
                    liq_price = compute_liquidation_price(
                        side, fill_price, leverage, fill.notional_usdt, margin_tiers_json
                    )
                    slippage_entry = fill.notional_usdt * slippage_frac
                    position = _Position(
                        side=side, entry_time_ms=open_times[i], entry_price=fill_price,
                        qty=fill.qty, margin_usdt=margin_usdt, notional=fill.notional_usdt,
                        leverage=leverage, fee_entry_usdt=fill.fee_entry_usdt, sl_price=sl,
                        original_sl_price=sl, tp_price=tp, trailing_distance=trailing,
                        effective_stop=sl, best_price=fill_price, liq_price=liq_price,
                        slippage_entry_usdt=slippage_entry, sl_margin_loss_pct=sl_margin_loss_pct,
                    )
                pending_signal = None

        if position is None:
            sub_df_signal = df.iloc[max(0, i + 1 - MAX_LOOKBACK_BARS) : i + 1]
            signal = strategy.evaluate(sub_df_signal)
            pending_signal = signal if signal != Signal.HOLD else None
        else:
            pending_signal = None

    if position is not None:
        finalize(closes[-1], len(df) - 1, "END_OF_DATA")

    return trades, skipped
