"""Features de mercado para el prompt del LLM (subfase 3.6, fase iv; contexto
de tendencia agregado en la revision del piloto v1 -> signal_review_v2).

ATR, volatilidad, correlacion con BTC, contexto de tendencia (retornos de 24h/
72h y distancia a la EMA50, todo en 1h), funding y posiciones reales abiertas
-- docs/FASE3_6_LLM.md, seccion (a). Todo se lee de la cache local
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
# Revision del piloto v1 (docs/FASE3_6_LLM.md seccion k): el modelo se quejo
# de "sin datos de tendencia" en varias respuestas -- contexto de tendencia en
# el mismo timeframe de referencia (1h), reutilizando las velas que ya se
# piden para ATR/correlacion (RETURNS_WINDOW_LONG + 1 = 101 alcanza para las 3).
RETURN_WINDOW_24H_BARS = 24
RETURN_WINDOW_72H_BARS = 72
EMA_TREND_PERIOD = 50


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
    Wilder) relativo al ultimo cierre, en PORCENTAJE (0,28 = 0,28 %).

    Revision del piloto v2 (docs/FASE3_6_LLM.md seccion k): esta funcion
    devolvia la FRACCION cruda (0,0028 = 0,28 %) pese a que su nombre --y la
    clave `volatility_atr14_pct` del mensaje-- dicen "pct". El modelo la leyo
    literal como porcentaje ("ATR ~0,006 %") y rechazo señales por "volatilidad
    muy baja" cuando en realidad era ~100 veces mayor. Mismo tipo de error que
    el funding de v1 (unidad inconsistente con el nombre del campo).

    `None` si faltan velas (hacen falta 15: 14 rangos, cada uno necesita el
    cierre anterior)."""
    if len(bars) < ATR_PERIOD + 1:
        return None
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(true_ranges[-ATR_PERIOD:]) / ATR_PERIOD
    last_close = bars[-1].close
    return (atr / last_close) * 100 if last_close else None


def compute_returns_std_pct(bars: list[OHLCVBar], n: int = RETURNS_WINDOW_SHORT) -> float | None:
    """Desviacion tipica de los ultimos `n` retornos logaritmicos, en
    PORCENTAJE (misma revision del piloto v2 que `compute_atr14_pct`: antes
    devolvia la fraccion cruda bajo la clave `returns_std_30`, sin sufijo
    `_pct` y sin convertir -- ahora la clave es `returns_std_30_pct`). `None`
    si faltan velas (hacen falta `n + 1` cierres para `n` retornos)."""
    if len(bars) < n + 1:
        return None
    closes = [b.close for b in bars[-(n + 1):]]
    returns = _log_returns(closes)
    if len(returns) < n:
        return None
    return statistics.stdev(returns) * 100


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


def compute_return_pct(bars: list[OHLCVBar], bars_back: int) -> float | None:
    """Retorno simple (no logaritmico) entre el cierre de hace `bars_back`
    velas de `REFERENCE_TIMEFRAME` (1h) y el ultimo cierre, en PORCENTAJE.
    `None` si no hay al menos `bars_back + 1` velas."""
    if len(bars) < bars_back + 1:
        return None
    previous = bars[-(bars_back + 1)].close
    if previous == 0:
        return None
    latest = bars[-1].close
    return (latest - previous) / previous * 100


def compute_ema_distance_pct(bars: list[OHLCVBar], period: int = EMA_TREND_PERIOD) -> float | None:
    """Distancia porcentual del ultimo cierre a su EMA(`period`) en
    `REFERENCE_TIMEFRAME` (1h): positivo = precio por encima de la media.
    Misma formula que `app.indicators.engine.ema` (`ewm(span=period,
    adjust=False)`), aproximada sobre la ventana cacheada disponible -- igual
    simplificacion documentada que el resto de este modulo (docstring de
    arriba). `None` si no hay al menos `period` velas."""
    if len(bars) < period:
        return None
    closes = [b.close for b in bars]
    alpha = 2.0 / (period + 1)
    ema_value = closes[0]
    for close in closes[1:]:
        ema_value = alpha * close + (1 - alpha) * ema_value
    if ema_value == 0:
        return None
    return (closes[-1] - ema_value) / ema_value * 100


def compute_trend_features(bars: list[OHLCVBar]) -> dict:
    """Contexto de tendencia en 1h (seccion a, revision del piloto v1):
    retorno de 24h y de 72h, y distancia porcentual a la EMA(50) de 1h."""
    return {
        "return_24h_pct": compute_return_pct(bars, RETURN_WINDOW_24H_BARS),
        "return_72h_pct": compute_return_pct(bars, RETURN_WINDOW_72H_BARS),
        "distance_to_ema50_1h_pct": compute_ema_distance_pct(bars),
    }


async def compute_funding_features(
    db: Database, symbol: str, candle_close_ms: int, funding_stale_margin_hours: float,
) -> dict:
    """Tasa de funding vigente en la vela evaluada (el ultimo evento cacheado
    con `funding_time <= candle_close_ms`), el intervalo del contrato en horas,
    y si esa tasa esta obsoleta (mas vieja que el intervalo mas el margen de
    `funding_stale_margin_hours`). `funding_rate_pct` esta en PORCENTAJE
    (0,01 = 0,01 %), no en la fraccion cruda que guarda `funding_cache`.
    `funding_rate_pct` y `funding_is_approximated` salen `None` si no hay
    ningun evento cacheado hasta ese instante."""
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
        # `funding_cache` guarda la fraccion cruda de Bitunix (0,0001 = 0,01 %);
        # `* 100` para porcentaje, igual que `signal_generator.py` linea ~351
        # (`signals.funding_rate_pct`) -- mismo nombre de campo, misma unidad.
        "funding_rate_pct": latest_rate * 100,
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
        "returns_std_30_pct": compute_returns_std_pct(bars_100, RETURNS_WINDOW_SHORT),
        "btc_correlation_30": await compute_btc_correlation(
            db, symbol, as_of_ms, RETURNS_WINDOW_SHORT
        ),
        "btc_correlation_100": await compute_btc_correlation(
            db, symbol, as_of_ms, RETURNS_WINDOW_LONG
        ),
        **compute_trend_features(bars_100),
        **funding,
        **positions,
    }
