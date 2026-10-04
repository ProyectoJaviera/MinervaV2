"""Generador de senales en vivo (Fase 3, subfase 3.3, punto 1 de
`docs/FASE3_PLAN.md`) -- reemplaza el viejo `Scheduler._evaluate_symbol`
(UNA estrategia sobre UN simbolo fijo, vestigio de Fase 1) por un
generador que, en cada tick, itera TODO el universo vigente
(`asset_universe` con `included=1`, nunca `settings.symbols`) x TODAS las
estrategias de `app/strategies/registry.py::STRATEGIES` (en sus timeframes),
llamando `strategy.precompute(df)` + `strategy.evaluate(df)` -- el MISMO
codigo de indicadores que el backtest.

**Solo velas cerradas**: `drop_incomplete_last_bar` descarta la vela en
formacion antes de evaluar nada.

**Una fila por (simbolo, estrategia, timeframe, vela), sin duplicados**:
cada evaluacion (HOLD incluido) se inserta en `signals` (`UNIQUE` +
`INSERT OR IGNORE`). Si la vela ya se evaluo en un poll anterior, la senal
se salta por completo: nunca se reprocesa ni se intenta abrir otra vez.

**Vela obsoleta (`STALE_DATA`)**: si la ultima vela cerrada cerro hace mas
de un intervalo mas `CANDLE_STALE_TOLERANCE_SECONDS`, la evaluacion se
registra con `reason="CANDLE_STALE"`; una senal accionable queda
`status="STALE_DATA"` y NO llega a agruparse ni a ejecucion ni a sombra. Cada
fila escrita desde una vela obsoleta sube `system_state.signals_stale_data_count`.
Un fallo de red al descargar no detiene el ciclo: se evalua con la cache y la
vela, si esta atrasada, queda marcada asi.

**Funding (solo `funding_contrarian_*`)**: se refresca desde la API publica
con `download_missing_funding` en cada ciclo (solo descarga si el ultimo
evento es mas viejo que el intervalo del contrato + `FUNDING_STALE_MARGIN_HOURS`).
Si aun asi el ultimo evento es mas viejo que ese limite, o no hay ninguno, la
estrategia no se evalua: HOLD con `reason="FUNDING_STALE"`. Los bares posteriores
al ultimo evento real se marcan `funding_is_approximated=True` en vivo (la
funcion de backtest los daba por reales), para que nada los trate como dato real.

**Filtro de tope de SL**: toda senal accionable calcula `sl_margin_loss_pct`
con la misma formula del backtest (`app/trading/sl_calc.py`). Si supera
`LIVE_SL_MARGIN_CAP_PCT`, la fila queda `status="DISCARDED_SL_CAP"` y se
audita en `signals_discarded_by_sl_cap`.

**Deduplicacion entre estrategias** (adelantado de 3.5 a pedido explicito):
las accionables que sobreviven se agrupan por (simbolo, direccion, vela) en
un `SignalCandidate` con `contributing_strategies`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from app.backtesting.funding import build_funding_series
from app.config import Settings
from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.market.contract_specs import refresh_spec
from app.market.funding_history import download_missing_funding
from app.market.ohlcv_history import (
    download_missing,
    drop_incomplete_last_bar,
    interval_to_ms,
)
from app.market.universe import is_universe_stale
from app.persistence.database import Database
from app.persistence.models import Side, SignalDiscardedBySLCap, SignalRecord
from app.persistence.repositories import (
    funding_repo,
    ohlcv_repo,
    signals_repo,
    specs_repo,
    system_state_repo,
    universe_repo,
)
from app.strategies.base import BaseStrategy, Signal
from app.strategies.registry import EXPERIMENTAL_STRATEGIES, STRATEGIES, STRATEGY_TIMEFRAMES
from app.trading.levels import StrategyLevels
from app.trading.sl_calc import fallback_sl_tp_prices, margin_loss_pct

logger = get_logger(__name__)

# Velas de historia pedidas por defecto -- misma cota de convergencia que
# el backtest (`MAX_LOOKBACK_BARS`): los indicadores usados convergen dentro de
# unas pocas veces su periodo.
DEFAULT_HISTORY_BARS = 300

# `funding_contrarian_percentile_experimental` necesita su ventana de
# referencia completa (HISTORY_WINDOW=720 + FUNDING_LOOKBACK=8), 300 no le alcanza.
_HISTORY_BARS_OVERRIDES = {"funding_contrarian_percentile_experimental": 750}

# Las unicas 2 estrategias que leen una columna `funding_rate`.
_FUNDING_STRATEGIES = {
    "funding_contrarian_experimental",
    "funding_contrarian_percentile_experimental",
}

FUNDING_DEFAULT_INTERVAL_HOURS = 8.0
SPEC_MAX_AGE_MS = 24 * 60 * 60 * 1000
_OHLCV_PRICE_TYPE = "LAST_PRICE"  # mismo price_type que el backtest usa para evaluar estrategias.
STALE_COUNT_KEY = "signals_stale_data_count"

REASON_CANDLE_STALE = "CANDLE_STALE"
REASON_FUNDING_STALE = "FUNDING_STALE"


def _history_bars_for(strategy_name: str) -> int:
    return _HISTORY_BARS_OVERRIDES.get(strategy_name, DEFAULT_HISTORY_BARS)


def _timeframe_groups() -> dict[str, list[str]]:
    """Invierte `STRATEGY_TIMEFRAMES` (estrategia -> timeframes) a
    (timeframe -> estrategias) para pedir las velas de cada timeframe UNA
    sola vez por simbolo."""
    groups: dict[str, list[str]] = {}
    for name, timeframes in STRATEGY_TIMEFRAMES.items():
        for tf in timeframes:
            groups.setdefault(tf, []).append(name)
    return groups


async def get_stale_data_count(db: Database) -> int:
    raw = await system_state_repo.get_state(db, STALE_COUNT_KEY)
    return int(raw) if raw else 0


async def _bump_stale_data_count(db: Database) -> None:
    await system_state_repo.set_state(db, STALE_COUNT_KEY, str(await get_stale_data_count(db) + 1))


@dataclass
class SignalCandidate:
    """Una oportunidad de mercado agrupada -- 1+ estrategias coincidieron
    en (simbolo, direccion, vela). `sl_margin_loss_pct` es el PEOR (mayor)
    entre las contribuyentes -- nunca se subestima el riesgo real."""

    symbol: str
    side: Side
    candle_close_time: datetime
    contributing_strategies: list[str]
    sl_margin_loss_pct: float
    # Niveles crudos (sin completar con el respaldo) de cada contribuyente: el
    # scheduler elige los de la estrategia representante y los completa al precio
    # real de llenado (ver `app/trading/levels.py`).
    levels_by_strategy: dict[str, StrategyLevels] = field(default_factory=dict)
    # Precio de cierre de la vela evaluada por cada contribuyente: precio de entrada
    # de la operacion sombra (mismo instante que la senal).
    price_by_strategy: dict[str, float] = field(default_factory=dict)


@dataclass
class _ActionableSignal:
    symbol: str
    side: Side
    candle_close_time: datetime
    strategy: str
    sl_margin_loss_pct: float
    levels: StrategyLevels
    price_at_eval: float


def pick_representative_strategy(contributing_strategies: list[str], settings: Settings) -> str:
    """Estrategia a la que se atribuye la apertura real cuando 2+ contribuyeron:
    la primera (orden alfabetico, determinista) que SI este en
    `REAL_ACCOUNT_ELIGIBLE_STRATEGIES`; si ninguna lo esta (o la lista esta
    vacia), la primera en orden alfabetico -- el motor de riesgo audita el
    rechazo correctamente en cualquier caso."""
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
        levels = {i.strategy: i.levels for i in group_items}
        prices = {i.strategy: i.price_at_eval for i in group_items}
        candidates.append(
            SignalCandidate(
                symbol, side, candle_close_time, contributing, worst_sl_pct, levels, prices,
            )
        )
    return candidates


async def _refresh_funding(
    db: Database, rest_client: BitunixRestClient, settings: Settings, symbol: str, now_ms: int,
) -> float:
    """Refresca la especificacion del contrato (intervalo de funding) si falta
    o tiene mas de un dia, y repone la cola de funding desde la API publica si
    esta obsoleta. Nunca lanza: un fallo de red solo deja la cache como esta, y
    la comprobacion de antiguedad se encarga de bloquear la estrategia."""
    spec = await specs_repo.get_spec(db, symbol)
    if spec is None or now_ms - int(spec.fetched_at.timestamp() * 1000) > SPEC_MAX_AGE_MS:
        try:
            spec = await refresh_spec(rest_client, db, symbol)
        except Exception:
            logger.exception("No se pudo refrescar el contrato de %s", symbol)

    interval_h = (
        float(spec.funding_interval_hours)
        if spec and spec.funding_interval_hours
        else FUNDING_DEFAULT_INTERVAL_HOURS
    )
    tolerance_ms = int((interval_h + settings.funding_stale_margin_hours) * 3_600_000)
    lookback_ms = max(_history_bars_for(n) for n in _FUNDING_STRATEGIES) * interval_to_ms("4h")
    try:
        await download_missing_funding(
            rest_client, db, symbol, now_ms - lookback_ms, now_ms,
            freshness_tolerance_ms=tolerance_ms,
        )
    except Exception:
        logger.exception("No se pudo refrescar el funding de %s; se usa la cache", symbol)
    return interval_h


async def _attach_funding_rate(
    db: Database, symbol: str, df: pd.DataFrame, interval_h: float,
    settings: Settings, now_ms: int,
) -> tuple[pd.DataFrame, bool]:
    """Agrega `funding_rate` y `funding_is_approximated` alineadas 1:1 con
    `df`. Devuelve (df, fresco): `fresco` es False si el ultimo evento cacheado
    es mas viejo que el intervalo del contrato mas el margen, o si no hay
    ninguno. En vivo, los bares posteriores al ultimo evento se marcan como
    aproximados (`build_funding_series` los daria por reales)."""
    bar_open_times = [int(t) for t in df["open_time"]]
    events = await funding_repo.get_funding(db, symbol, bar_open_times[0], now_ms)
    rates, approx_flags = build_funding_series(bar_open_times, events)

    last_event_ms = events[-1][0] if events else None
    max_age_ms = (interval_h + settings.funding_stale_margin_hours) * 3_600_000
    fresh = last_event_ms is not None and now_ms - last_event_ms <= max_age_ms

    live_approx = [
        approx or last_event_ms is None or t > last_event_ms
        for t, approx in zip(bar_open_times, approx_flags, strict=True)
    ]
    df = df.copy()
    df["funding_rate"] = rates
    df["funding_is_approximated"] = live_approx
    return df, fresh


async def _load_closed_bars(
    db: Database, rest_client: BitunixRestClient, symbol: str, timeframe: str,
    bars_needed: int, now_ms: int,
) -> pd.DataFrame:
    """Descarga incremental (si hay red) y lee SOLO velas cerradas de la cache.
    Un fallo de descarga NO impide leer la cache: si esta atrasada, la evaluacion
    queda marcada como vela obsoleta, en vez de desaparecer en silencio."""
    step_ms = interval_to_ms(timeframe)
    if step_ms is None:
        raise ValueError(f"Intervalo desconocido: {timeframe}")
    start_ms = now_ms - bars_needed * step_ms

    try:
        await download_missing(
            rest_client, db, symbol, timeframe, start_ms, now_ms,
            price_type=_OHLCV_PRICE_TYPE,
        )
    except Exception:
        logger.warning("No se pudieron descargar velas de %s %s; se usa la cache",
                       symbol, timeframe)

    bars = await ohlcv_repo.get_bars(
        db, symbol, timeframe, _OHLCV_PRICE_TYPE, start_ms, now_ms
    )
    bars = drop_incomplete_last_bar(bars, timeframe, now_ms)
    if len(bars) < 2:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])

    df = pd.DataFrame([b.model_dump() for b in bars]).sort_values("open_time")
    return df.reset_index(drop=True)


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
    *,
    blocked_reason: str | None = None,
    candle_stale: bool = False,
) -> _ActionableSignal | None:
    """Evalua UNA estrategia, inserta la fila en `signals` (si la vela ya existia,
    se salta todo lo demas) y devuelve la senal accionable que sobrevivio, o
    `None`. `blocked_reason` fuerza HOLD sin evaluar (p.ej. funding obsoleto);
    `candle_stale` marca la fila y veta cualquier senal accionable."""
    evaluated_at = datetime.now(UTC)
    price_at_eval = float(df["close"].iloc[-1])
    reason = REASON_CANDLE_STALE if candle_stale else blocked_reason

    if blocked_reason is not None:
        signal = Signal.HOLD
    else:
        df = strategy.precompute(df)
        signal = strategy.evaluate(df)

    stop_price = take_profit_price = trailing_distance = None
    sl_margin_pct: float | None = None
    status: str | None = None
    raw_levels = StrategyLevels()

    if signal != Signal.HOLD:
        stop_price = strategy.stop_price(df, signal, price_at_eval)
        take_profit_price = strategy.take_profit_price(df, signal, price_at_eval)
        trailing_distance = strategy.trailing_distance(df)
        raw_levels = StrategyLevels(stop_price, take_profit_price, trailing_distance)
        fallback_sl, fallback_tp = fallback_sl_tp_prices(signal, price_at_eval, settings)
        if stop_price is None:
            stop_price = fallback_sl
        if take_profit_price is None and trailing_distance is None:
            take_profit_price = fallback_tp
        sl_margin_pct = margin_loss_pct(price_at_eval, stop_price, settings.leverage)
        status = "STALE_DATA" if candle_stale else "PENDING"

    funding_rate_pct = None
    funding_is_approximated = False
    if "funding_rate" in df.columns and not pd.isna(df["funding_rate"].iloc[-1]):
        funding_rate_pct = float(df["funding_rate"].iloc[-1]) * 100
        funding_is_approximated = bool(df["funding_is_approximated"].iloc[-1])

    record = SignalRecord(
        symbol=symbol, strategy=strategy_name,
        is_experimental=strategy_name in EXPERIMENTAL_STRATEGIES,
        timeframe=timeframe, candle_close_time=candle_close_time, evaluated_at=evaluated_at,
        signal=signal.value, price_at_eval=price_at_eval, stop_price=stop_price,
        take_profit_price=take_profit_price, trailing_distance=trailing_distance,
        funding_rate_pct=funding_rate_pct, funding_is_approximated=funding_is_approximated,
        sl_margin_loss_pct=sl_margin_pct, indicators_json=_serialize_indicators(df.iloc[-1]),
        status=status, reason=reason,
    )
    signal_id = await signals_repo.insert_signal(db, record)
    if signal_id is None:
        return None  # vela ya evaluada en un poll anterior -- nunca se reprocesa
    if candle_stale:
        await _bump_stale_data_count(db)

    if signal == Signal.HOLD or candle_stale:
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
        strategy=strategy_name, sl_margin_loss_pct=sl_margin_pct, levels=raw_levels,
        price_at_eval=price_at_eval,
    )


async def run_signal_generation_cycle(
    db: Database, rest_client: BitunixRestClient, settings: Settings,
) -> list[SignalCandidate]:
    """Un ciclo completo: universo x estrategias x timeframes -> filas en `signals`
    -> filtros de vela y funding obsoletos y tope de SL -> candidatos agrupados.
    Cada (simbolo, timeframe) aisla sus propios errores."""
    if await is_universe_stale(db, settings):
        logger.warning("Universo obsoleto o ausente (> %dh): no se generan senales hasta el "
                       "proximo refresco. Los cierres no se ven afectados.",
                       settings.universe_staleness_hours)
        return []
    symbols = await universe_repo.get_included_symbols(db)
    if not symbols:
        logger.debug("Universo vacio -- sin simbolos para generar senales todavia.")
        return []

    now_ms = int(time.time() * 1000)
    tolerance_ms = int(settings.candle_stale_tolerance_seconds * 1000)
    groups = _timeframe_groups()
    actionable: list[_ActionableSignal] = []

    for symbol in symbols:
        interval_h = await _refresh_funding(db, rest_client, settings, symbol, now_ms)

        for timeframe, strategy_names in groups.items():
            bars_needed = max(_history_bars_for(name) for name in strategy_names)
            try:
                df = await _load_closed_bars(
                    db, rest_client, symbol, timeframe, bars_needed, now_ms
                )
            except Exception:
                logger.exception("No se pudieron leer velas de %s %s", symbol, timeframe)
                continue
            if len(df) < 2:
                continue

            step_ms = interval_to_ms(timeframe)
            assert step_ms is not None
            candle_close_ms = int(df["open_time"].iloc[-1]) + step_ms
            candle_stale = now_ms - candle_close_ms > step_ms + tolerance_ms
            candle_close_time = datetime.fromtimestamp(candle_close_ms / 1000, tz=UTC)

            funding_df: pd.DataFrame | None = None
            funding_fresh = False
            if any(name in _FUNDING_STRATEGIES for name in strategy_names):
                try:
                    funding_df, funding_fresh = await _attach_funding_rate(
                        db, symbol, df, interval_h, settings, now_ms
                    )
                except Exception:
                    logger.exception("No se pudo adjuntar funding a %s", symbol)

            for strategy_name in strategy_names:
                strategy = STRATEGIES[strategy_name]
                base_df = df
                blocked = None
                if strategy_name in _FUNDING_STRATEGIES:
                    if funding_df is not None and funding_fresh:
                        base_df = funding_df
                    else:
                        blocked = REASON_FUNDING_STALE
                try:
                    result = await _evaluate_one(
                        db, settings, strategy, strategy_name, symbol, timeframe, base_df,
                        candle_close_time, blocked_reason=blocked, candle_stale=candle_stale,
                    )
                except Exception:
                    logger.exception("Error evaluando %s %s %s", symbol, strategy_name, timeframe)
                    continue
                if result is not None:
                    actionable.append(result)

    return _group_actionable_signals(actionable)
