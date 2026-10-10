"""Features de mercado para el prompt del LLM (subfase 3.6, fase iv).

ATR, volatilidad, correlacion con BTC, funding y posiciones reales abiertas --
docs/FASE3_6_LLM.md, seccion (a), puntos 4-7. Todo se lee de la cache local
(`ohlcv_cache`, `funding_cache`, `contract_specs_cache`, `trades`); este modulo
NUNCA toca la red.

**Prueba de fuga (obligatoria, seccion a):** cada funcion que lee velas recibe
`as_of_ms`, el cierre de la vela evaluada, y solo usa velas CERRADAS antes o
en ese instante (`open_time + duracion <= as_of_ms`) -- nunca una vela que
todavia no cerro ahi. `tests/unit/test_llm_market_features.py` prueba esto con
velas futuras de valores extremos (no cambian el resultado) y comparando la
serie completa contra la truncada.

**Timeframe de referencia fijo: "1h".** Las features de mercado (ATR,
volatilidad, correlacion con BTC) se calculan siempre en 1h, sin importar el
timeframe propio de cada estrategia contribuyente (esas van en
`indicators_by_strategy`, ver `app/llm/prompts.py`). Es una simplificacion
documentada: 1h es el timeframe mas fino que ya se cachea para TODO el
universo (`mean_reversion_rsi14_bb20` lo evalua para cada simbolo en cada
ciclo, `app/strategies/registry.py`), asi que no agrega descargas nuevas.

Si falta un dato (velas insuficientes, sin funding cacheado), la funcion
devuelve `None` explicito -- nunca se inventa ni se rellena con un valor
supuesto.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

from app.market.ohlcv_history import interval_to_ms
from app.persistence.database import Database
from app.persistence.models import OHLCVBar, Side, Trade
from app.persistence.repositories import funding_repo, ohlcv_repo, specs_repo, trades_repo

REFERENCE_TIMEFRAME = "1h"
REFERENCE_PRICE_TYPE = "LAST_PRICE"
ATR_PERIOD = 14
RETURNS_WINDOW_SHORT = 30
RETURNS_WINDOW_LONG = 100
DEFAULT_FUNDING_INTERVAL_HOURS = 8.0


async def fetch_closed_candles(
    db: Database, symbol: str, as_of_ms: int, limit: int,
    timeframe: str = REFERENCE_TIMEFRAME, price_type: str = REFERENCE_PRICE_TYPE,
) -> list[OHLCVBar]:
    """Las ultimas `limit` velas de `timeframe`, CERRADAS antes o en `as_of_ms`
    (`open_time + duracion <= as_of_ms`), ordenadas por `open_time` ascendente.
    Lista vacia si no hay suficientes en la cache -- no descarga nada."""
    step_ms = interval_to_ms(timeframe)
    if step_ms is None:
        return []
    # Pide de mas por si hay huecos en la cache; se recorta a `limit` al final.
    start_ms = as_of_ms - (limit + 5) * step_ms
    bars = await ohlcv_repo.get_bars(db, symbol, timeframe, price_type, start_ms, as_of_ms)
    closed = [b for b in bars if b.open_time + step_ms <= as_of_ms]
    closed.sort(key=lambda b: b.open_time)
    return closed[-limit:]


def _log_returns(closes: list[float]) -> list[float]:
    return [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]


def compute_atr14_pct(bars: list[OHLCVBar]) -> float | None:
    """ATR(14) simple (promedio de 14 rangos verdaderos; no el suavizado de
    Wilder) relativo al ultimo cierre, como fraccion. `None` si faltan velas
    (hacen falta 15: 14 rangos, cada uno necesita el cierre anterior)."""
    if len(bars) < ATR_PERIOD + 1:
        return None
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(true_ranges[-ATR_PERIOD:]) / ATR_PERIOD
    last_close = bars[-1].close
    return atr / last_close if last_close else None


def compute_returns_std(bars: list[OHLCVBar], n: int = RETURNS_WINDOW_SHORT) -> float | None:
    """Desviacion tipica de los ultimos `n` retornos logaritmicos. `None` si
    faltan velas (hacen falta `n + 1` cierres para `n` retornos)."""
    if len(bars) < n + 1:
        return None
    closes = [b.close for b in bars[-(n + 1):]]
    returns = _log_returns(closes)
    if len(returns) < n:
        return None
    return statistics.stdev(returns)


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / n
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs) / n)
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys) / n)
    if std_x == 0 or std_y == 0:
        return None
    return cov / (std_x * std_y)


async def compute_btc_correlation(db: Database, symbol: str, as_of_ms: int, n: int) -> float | None:
    """Correlacion de RETORNOS (no de precios) entre `symbol` y BTCUSDT, sobre
    las ultimas `n` velas cerradas de ambos, alineadas por `open_time`
    (interseccion de marcas de tiempo). `None` si no se alcanzan `n` velas
    alineadas. Para `symbol = "BTCUSDT"` sale 1.0 (autocorrelacion), no es un
    caso especial."""
    own_bars = await fetch_closed_candles(db, symbol, as_of_ms, n + 1)
    btc_bars = await fetch_closed_candles(db, "BTCUSDT", as_of_ms, n + 1)
    own_by_time = {b.open_time: b.close for b in own_bars}
    btc_by_time = {b.open_time: b.close for b in btc_bars}
    common_times = sorted(set(own_by_time) & set(btc_by_time))
    if len(common_times) < n + 1:
        return None
    common_times = common_times[-(n + 1):]
    own_closes = [own_by_time[t] for t in common_times]
    btc_closes = [btc_by_time[t] for t in common_times]
    own_returns = _log_returns(own_closes)
    btc_returns = _log_returns(btc_closes)
    if len(own_returns) < n or len(btc_returns) < n:
        return None
    return _pearson_correlation(own_returns, btc_returns)


async def compute_funding_features(
    db: Database, symbol: str, candle_close_ms: int, funding_stale_margin_hours: float,
) -> dict:
    """Tasa de funding vigente en la vela evaluada (el ultimo evento cacheado
    con `funding_time <= candle_close_ms`), el intervalo del contrato en horas,
    y si esa tasa esta obsoleta (mas vieja que el intervalo mas el margen de
    `funding_stale_margin_hours`). `funding_rate_pct` y `funding_is_approximated`
    salen `None` si no hay ningun evento cacheado hasta ese instante."""
    spec = await specs_repo.get_spec(db, symbol)
    interval_h = (
        float(spec.funding_interval_hours)
        if spec and spec.funding_interval_hours
        else DEFAULT_FUNDING_INTERVAL_HOURS
    )
    events = await funding_repo.get_funding(db, symbol, 0, candle_close_ms)
    if not events:
        return {
            "funding_rate_pct": None,
            "funding_interval_hours": interval_h,
            "funding_is_approximated": None,
        }
    latest_time, latest_rate = events[-1]
    tolerance_ms = (interval_h + funding_stale_margin_hours) * 3_600_000
    return {
        "funding_rate_pct": latest_rate,
        "funding_interval_hours": interval_h,
        "funding_is_approximated": (candle_close_ms - latest_time) > tolerance_ms,
    }


def _position_summary(positions: list[Trade], symbol: str) -> dict:
    return {
        "open_real_positions_total": len(positions),
        "open_real_positions_same_symbol": sum(1 for p in positions if p.symbol == symbol),
        "open_real_positions_long": sum(1 for p in positions if p.side == Side.LONG),
        "open_real_positions_short": sum(1 for p in positions if p.side == Side.SHORT),
    }


async def compute_open_real_positions(db: Database, symbol: str, as_of: datetime) -> dict:
    """Posiciones REALES (tabla `trades`) abiertas en el instante `as_of` --
    numero total, cuantas del mismo `symbol`, y cuantas LONG/SHORT. Las
    operaciones sombra NUNCA entran aqui (seccion a, punto 7)."""
    positions = await trades_repo.get_open_positions_as_of(db, as_of)
    return _position_summary(positions, symbol)


async def build_market_features(
    db: Database, symbol: str, candle_close_time: datetime, funding_stale_margin_hours: float,
) -> dict:
    """Junta todas las features de mercado (seccion a, puntos 4-7) para una
    senal evaluada en `candle_close_time`. Solo usa datos con cierre o
    instante <= esa vela."""
    as_of_ms = int(candle_close_time.timestamp() * 1000)
    bars_100 = await fetch_closed_candles(db, symbol, as_of_ms, RETURNS_WINDOW_LONG + 1)

    funding = await compute_funding_features(
        db, symbol, as_of_ms, funding_stale_margin_hours
    )
    positions = await compute_open_real_positions(db, symbol, candle_close_time)

    return {
        "volatility_atr14_pct": compute_atr14_pct(bars_100),
        "returns_std_30": compute_returns_std(bars_100, RETURNS_WINDOW_SHORT),
        "btc_correlation_30": await compute_btc_correlation(
            db, symbol, as_of_ms, RETURNS_WINDOW_SHORT
        ),
        "btc_correlation_100": await compute_btc_correlation(
            db, symbol, as_of_ms, RETURNS_WINDOW_LONG
        ),
        **funding,
        **positions,
    }
