"""Generador de senales en vivo (Fase 3, subfase 3.3, punto 1 de
`docs/FASE3_PLAN.md`) -- reemplaza el viejo `Scheduler._evaluate_symbol`
(UNA estrategia sobre UN simbolo fijo, vestigio de Fase 1) por un
generador que, en cada tick, itera TODO el universo vigente
(`asset_universe` con `included=1`, nunca `settings.symbols`) x TODAS las
estrategias de `app/strategies/registry.py::STRATEGIES` en sus timeframes
(`STRATEGY_TIMEFRAMES`), llamando `strategy.precompute(df)` +
`strategy.evaluate(df)` -- el MISMO codigo de indicadores que el backtest,
cero logica nueva ahi.

**Solo velas cerradas**: `app.market.ohlcv_history.drop_incomplete_last_bar`
descarta la vela en formacion antes de evaluar nada -- nunca se opera sobre
un precio que todavia puede moverse dentro de la misma vela.

**Una fila por (simbolo, estrategia, timeframe, vela), sin duplicados**:
cada evaluacion (HOLD incluido) se inserta en la tabla `signals`
(`UNIQUE(symbol, strategy, timeframe, candle_close_time)` + `INSERT OR
IGNORE`, ver `signals_repo.insert_signal`) -- si la misma vela cerrada ya
se evaluo en un poll anterior, `insert_signal` devuelve `None` y esta
senal se salta por completo (nunca se reprocesa ni se re-intenta abrir una
posicion por ella).

**Filtro de tope de SL** (docs/FASE3_PLAN.md punto 2, primero y para
todos por igual): toda senal accionable (LONG/SHORT) calcula
`sl_margin_loss_pct` con la MISMA formula que el backtest
(`app/trading/sl_calc.py`, extraida de `app/backtesting/engine.py`) -- si
supera `Settings.live_sl_margin_cap_pct`, la fila de `signals` queda
`status="DISCARDED_SL_CAP"` y se audita en `signals_discarded_by_sl_cap`;
nunca llega a agruparse ni a intentar abrir una posicion.

**Deduplicacion de senales simultaneas entre estrategias** (adelantado de
la subfase 3.5 por pedido explicito del usuario, porque el generador de
esta subfase YA intenta abrir posiciones reales): las senales accionables
que sobreviven el filtro de SL se agrupan por (simbolo, direccion,
vela_de_cierre) -- si 2+ estrategias coinciden, se tratan como UN solo
candidato (`SignalCandidate.contributing_strategies`) en vez de abrir 2-3
posiciones por la misma oportunidad de mercado. El desglose COMPLETO por
estrategia (atribuir el resultado a cada contribuyente, no solo a una)
pertenece al diseno de `shadow_trades` de la subfase 3.5 -- aqui solo se
usa para no duplicar la apertura en la cuenta real; `_pick_representative_strategy`
documenta la regla de eleccion usada mientras tanto.

**Funding para `funding_contrarian_*`**: se lee de `funding_cache` (la
MISMA cache que ya llena `scripts/download_history.py` para el backtest) --
este modulo NUNCA descarga funding por red. Si la cache esta vacia o
desactualizada para un simbolo nuevo del universo, la estrategia
simplemente no opera (HOLD, degradacion ya incorporada en su diseno) en
vez de fallar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from app.backtesting.funding import build_funding_series
from app.config import Settings
from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.market.ohlcv_history import (
    download_missing,
    drop_incomplete_last_bar,
    get_cached_or_raise,
    interval_to_ms,
)
from app.persistence.database import Database
from app.persistence.models import Side, SignalDiscardedBySLCap, SignalRecord
from app.persistence.repositories import funding_repo, signals_repo, universe_repo
from app.strategies.base import BaseStrategy, Signal
from app.strategies.registry import EXPERIMENTAL_STRATEGIES, STRATEGIES, STRATEGY_TIMEFRAMES
from app.trading.sl_calc import fallback_sl_tp_prices, margin_loss_pct

logger = get_logger(__name__)

# Velas de historia pedidas por defecto -- misma cota de convergencia que
# el backtest (`MAX_LOOKBACK_BARS` en `app/backtesting/engine.py`): los
# indicadores usados (EMA/RSI/ATR/Bollinger/Donchian, periodo maximo 50)
# convergen numericamente dentro de unas pocas veces su periodo.
DEFAULT_HISTORY_BARS = 300

# `funding_contrarian_percentile_experimental` necesita su ventana de
# referencia completa (HISTORY_WINDOW=720 + FUNDING_LOOKBACK=8, ver
# `app/strategies/funding_contrarian_percentile.py`) para producir UN solo
# valor no-NaN en la ultima fila -- 300 velas nunca le alcanzan.
_HISTORY_BARS_OVERRIDES = {"funding_contrarian_percentile_experimental": 750}

# Las unicas 2 estrategias que leen una columna `funding_rate` -- las
# demas ignoran el funding por completo.
_FUNDING_STRATEGIES = {
    "funding_contrarian_experimental",
    "funding_contrarian_percentile_experimental",
}

_OHLCV_PRICE_TYPE = "LAST_PRICE"  # mismo price_type que el backtest usa para evaluar estrategias.


def _history_bars_for(strategy_name: str) -> int:
    return _HISTORY_BARS_OVERRIDES.get(strategy_name, DEFAULT_HISTORY_BARS)


def _timeframe_groups() -> dict[str, list[str]]:
    """Invierte `STRATEGY_TIMEFRAMES` (estrategia -> timeframes) a
    (timeframe -> estrategias) para pedir las velas de cada timeframe UNA
    sola vez por simbolo, aunque varias estrategias lo comparen."""
    groups: dict[str, list[str]] = {}
    for name, timeframes in STRATEGY_TIMEFRAMES.items():
        for tf in timeframes:
            groups.setdefault(tf, []).append(name)
    return groups


@dataclass
class SignalCandidate:
    """Una oportunidad de mercado agrupada -- 1+ estrategias coincidieron
    en (simbolo, direccion, vela). `sl_margin_loss_pct` es el PEOR (mayor)
    entre las contribuyentes -- nunca se subestima el riesgo real de la
    entrada agrupada."""

    symbol: str
    side: Side
    candle_close_time: datetime
    contributing_strategies: list[str]
    sl_margin_loss_pct: float


@dataclass
class _ActionableSignal:
    symbol: str
    side: Side
    candle_close_time: datetime
    strategy: str
    sl_margin_loss_pct: float


def pick_representative_strategy(
    contributing_strategies: list[str], settings: Settings
) -> str:
    """Elige que nombre de estrategia se le atribuye al intento de abrir
    la posicion REAL cuando 2+ estrategias contribuyeron (ver el docstring
    del modulo: el desglose completo por estrategia es diseno de la
    subfase 3.5). Regla: la primera (orden alfabetico, para que sea
    deterministico) que SI este en `REAL_ACCOUNT_ELIGIBLE_STRATEGIES`; si
    ninguna lo esta (o la lista esta vacia, sin restriccion), la primera en
    orden alfabetico sin mas -- el motor de riesgo ya audita
    correctamente el resultado en cualquier caso."""
    ordered = sorted(contributing_strategies)
    eligible = set(settings.real_account_eligible_strategies_list)
    if not eligible:
        return ordered[0]
    return next((s for s in ordered if s in eligible), ordered[0])


def _group_actionable_signals(items: list[_ActionableSignal]) -> list[SignalCandidate]:
    groups: dict[tuple[str, Side, datetime], list[_ActionableSignal]] = {}
    for item in items:
        key = (item.symbol, item.side, item.candle_close_time)
        groups.setdefault(key, []).append(item)

    candidates = []
    for (symbol, side, candle_close_time), group_items in groups.items():
        contributing = sorted({i.strategy for i in group_items})
        worst_sl_pct = max(i.sl_margin_loss_pct for i in group_items)
        candidates.append(
            SignalCandidate(symbol, side, candle_close_time, contributing, worst_sl_pct)
        )
    return candidates


async def _load_closed_bars(
    db: Database, rest_client: BitunixRestClient, symbol: str, timeframe: str, bars_needed: int,
) -> pd.DataFrame:
    """Descarga (incremental, cacheada) + lee SOLO velas CERRADAS. Reusa
    `app.market.ohlcv_history` -- el mismo cache que ya llena el backtest,
    asi que polls sucesivos de la misma vela no vuelven a pedirla por red."""
    step_ms = interval_to_ms(timeframe)
    if step_ms is None:
        raise ValueError(f"Intervalo desconocido: {timeframe}")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - bars_needed * step_ms

    await download_missing(
        rest_client, db, symbol, timeframe, start_ms, now_ms, price_type=_OHLCV_PRICE_TYPE
    )
    bars = await get_cached_or_raise(
        db, symbol, timeframe, start_ms, now_ms, price_type=_OHLCV_PRICE_TYPE
    )
    bars = drop_incomplete_last_bar(bars, timeframe, now_ms)
    if len(bars) < 2:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])

    df = pd.DataFrame([b.model_dump() for b in bars]).sort_values("open_time")
    return df.reset_index(drop=True)


async def _attach_funding_rate(db: Database, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """Agrega la columna `funding_rate` alineada 1:1 con `df` leyendo
    `funding_cache` (nunca red, ver el docstring del modulo) -- misma
    funcion que el backtest (`build_funding_series`)."""
    bar_open_times = [int(t) for t in df["open_time"]]
    events = await funding_repo.get_funding(db, symbol, bar_open_times[0], bar_open_times[-1])
    rates, approx_flags = build_funding_series(bar_open_times, events)
    df = df.copy()
    df["funding_rate"] = rates
    df["funding_is_approximated"] = approx_flags
    return df


def _serialize_indicators(row: pd.Series) -> str:
    base_cols = {
        "open_time", "open", "high", "low", "close", "base_vol", "quote_vol", "symbol",
        "interval", "price_type",
    }
    extra = {}
    for col in row.index:
        if col in base_cols:
            continue
        value = row[col]
        if pd.isna(value):
            extra[col] = None
        elif isinstance(value, bool):
            extra[col] = value
        else:
            extra[col] = float(value)
    return json.dumps(extra)


async def _evaluate_one(
    db: Database,
    settings: Settings,
    strategy: BaseStrategy,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    candle_close_time: datetime,
) -> _ActionableSignal | None:
    """Evalua UNA estrategia sobre el df ya preparado, inserta la fila en
    `signals` (saltandose todo lo demas si ya existia -- vela repetida) y,
    si es accionable, aplica el filtro de tope de SL. Devuelve la senal
    accionable que sobrevivio, o `None` (HOLD, duplicada, o descartada)."""
    df = strategy.precompute(df)
    signal = strategy.evaluate(df)
    price_at_eval = float(df["close"].iloc[-1])
    evaluated_at = datetime.now(UTC)

    stop_price = take_profit_price = trailing_distance = None
    sl_margin_pct: float | None = None
    status: str | None = None

    if signal != Signal.HOLD:
        stop_price = strategy.stop_price(df, signal, price_at_eval)
        take_profit_price = strategy.take_profit_price(df, signal, price_at_eval)
        trailing_distance = strategy.trailing_distance(df)
        fallback_sl, fallback_tp = fallback_sl_tp_prices(signal, price_at_eval, settings)
        if stop_price is None:
            stop_price = fallback_sl
        if take_profit_price is None and trailing_distance is None:
            take_profit_price = fallback_tp
        sl_margin_pct = margin_loss_pct(price_at_eval, stop_price, settings.leverage)
        status = "PENDING"

    funding_rate_pct = None
    funding_is_approximated = False
    if "funding_rate" in df.columns:
        last_funding = df["funding_rate"].iloc[-1]
        if not pd.isna(last_funding):
            funding_rate_pct = float(last_funding) * 100
        if "funding_is_approximated" in df.columns:
            funding_is_approximated = bool(df["funding_is_approximated"].iloc[-1])

    record = SignalRecord(
        symbol=symbol, strategy=strategy_name,
        is_experimental=strategy_name in EXPERIMENTAL_STRATEGIES,
        timeframe=timeframe, candle_close_time=candle_close_time, evaluated_at=evaluated_at,
        signal=signal.value, price_at_eval=price_at_eval, stop_price=stop_price,
        take_profit_price=take_profit_price, trailing_distance=trailing_distance,
        funding_rate_pct=funding_rate_pct, funding_is_approximated=funding_is_approximated,
        sl_margin_loss_pct=sl_margin_pct, indicators_json=_serialize_indicators(df.iloc[-1]),
        status=status,
    )
    signal_id = await signals_repo.insert_signal(db, record)
    if signal_id is None:
        # Vela ya evaluada en un poll anterior -- nunca se reprocesa.
        return None

    if signal == Signal.HOLD:
        return None

    assert sl_margin_pct is not None and stop_price is not None
    if sl_margin_pct > settings.live_sl_margin_cap_pct:
        await signals_repo.update_status(db, signal_id, "DISCARDED_SL_CAP")
        await signals_repo.insert_discarded_by_sl_cap(
            db,
            SignalDiscardedBySLCap(
                signal_id=signal_id, symbol=symbol, strategy=strategy_name, timeframe=timeframe,
                candle_close_time=candle_close_time, side=Side(signal.value),
                sl_margin_loss_pct=sl_margin_pct, cap_pct=settings.live_sl_margin_cap_pct,
                created_at=evaluated_at,
            ),
        )
        return None

    return _ActionableSignal(
        symbol=symbol, side=Side(signal.value), candle_close_time=candle_close_time,
        strategy=strategy_name, sl_margin_loss_pct=sl_margin_pct,
    )


async def run_signal_generation_cycle(
    db: Database, rest_client: BitunixRestClient, settings: Settings,
) -> list[SignalCandidate]:
    """Un ciclo completo del generador: universo x estrategias x
    timeframes -> filas en `signals` -> filtro de tope de SL -> candidatos
    agrupados. Cada (simbolo, timeframe) aisla sus propios errores (un
    simbolo con datos malos no debe tumbar el resto del ciclo, mismo
    patron que el `Scheduler` ya usaba)."""
    symbols = await universe_repo.get_included_symbols(db)
    if not symbols:
        logger.debug("Universo vacio -- sin simbolos para generar senales todavia.")
        return []

    groups = _timeframe_groups()
    actionable: list[_ActionableSignal] = []

    for symbol in symbols:
        for timeframe, strategy_names in groups.items():
            bars_needed = max(_history_bars_for(name) for name in strategy_names)
            try:
                df = await _load_closed_bars(db, rest_client, symbol, timeframe, bars_needed)
            except Exception:
                logger.exception("No se pudieron obtener velas de %s %s", symbol, timeframe)
                continue
            if len(df) < 2:
                continue

            if any(name in _FUNDING_STRATEGIES for name in strategy_names):
                try:
                    df = await _attach_funding_rate(db, symbol, df)
                except Exception:
                    logger.exception("No se pudo adjuntar funding para %s", symbol)

            step_ms = interval_to_ms(timeframe)
            assert step_ms is not None
            candle_close_time = datetime.fromtimestamp(
                (int(df["open_time"].iloc[-1]) + step_ms) / 1000, tz=UTC
            )

            for strategy_name in strategy_names:
                strategy = STRATEGIES[strategy_name]
                try:
                    result = await _evaluate_one(
                        db, settings, strategy, strategy_name, symbol, timeframe, df,
                        candle_close_time,
                    )
                except Exception:
                    logger.exception(
                        "Error evaluando %s %s %s", symbol, strategy_name, timeframe
                    )
                    continue
                if result is not None:
                    actionable.append(result)

    return _group_actionable_signals(actionable)
