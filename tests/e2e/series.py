"""Series de velas sinteticas y DETERMINISTAS para las pruebas de punta a punta.

No son constantes magicas: la serie de cada direccion se valida con las estrategias REALES del
registro como juez (`signals_on_last_bar`). Cada serie de 4 h termina en una vela cuyo cierre
dispara exactamente las senales pedidas y ninguna otra (aqui: un cruce de EMA 9/21 a favor de la
tendencia mayor, que dispara `ema_cross_9_21` y `trend_atr_stop_9_21_50` pero NO la ruptura
Donchian). Se parte de los parametros que ya se sabe que funcionan y, solo si un cambio futuro de
los indicadores los invalidara, se recorre una rejilla; si no existe ninguna serie valida falla
con un mensaje claro en vez de producir un escenario distinto en silencio.

Las funciones son puras (sin base de datos ni red) y se cachean.
"""

from __future__ import annotations

import functools
import math

import pandas as pd

from app.persistence.models import OHLCVBar, Side
from app.strategies.base import Signal
from app.strategies.registry import STRATEGIES
from app.trading import signal_generator as sg

# Estrategias de precio que corren sobre velas de 4 h (las de funding necesitan datos de funding).
PRICE_STRATEGIES_4H = ("ema_cross_9_21", "trend_atr_stop_9_21_50", "donchian_breakout_20")

BARS_4H = max(sg._history_bars_for(name) for name in sg._timeframe_groups()["4h"]) + 10
BARS_1H = max(sg._history_bars_for(name) for name in sg._timeframe_groups()["1h"]) + 10
BARS_1D = max(sg._history_bars_for(name) for name in sg._timeframe_groups()["1d"]) + 10

STEP_MS = {"1h": 3_600_000, "4h": 4 * 3_600_000, "1d": 24 * 3_600_000}

# (barras de retroceso, retroceso por barra, pasos de rebote de 0.05 %) que ya se sabe que dan un
# cruce a favor de la tendencia mayor sin ruptura Donchian.
_KNOWN_TREND_PARAMS = {Side.LONG: (6, 0.005, 30), Side.SHORT: (6, 0.005, 28)}


def bars_from_closes(
    closes: list[float], *, symbol: str, interval: str, end_ms: int, price_type: str = "LAST_PRICE",
) -> list[OHLCVBar]:
    """Velas CERRADAS cuya ultima cierra exactamente en `end_ms` (apertura = cierre anterior)."""
    step_ms = STEP_MS[interval]
    n = len(closes)
    bars = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        bars.append(OHLCVBar(
            symbol=symbol, interval=interval, price_type=price_type,
            open_time=end_ms - (n - i) * step_ms, open=open_,
            high=max(open_, close) * 1.0005, low=min(open_, close) * 0.9995, close=close,
            base_vol=1.0, quote_vol=close,
        ))
    return bars


MINUTE_MS = 60_000


def minute_bars(
    symbol: str, *, first_open_ms: int, last_open_ms: int, price: float = 100.0,
    overrides: dict[int, dict[str, float]] | None = None,
) -> list[OHLCVBar]:
    """Velas de 1 minuto (LAST_PRICE y MARK_PRICE) planas en `price` entre dos aperturas, salvo los
    campos de `overrides[open_time]`: es lo que el monitor reproduce al reconciliar la caida."""
    overrides = overrides or {}
    bars = []
    for price_type in ("LAST_PRICE", "MARK_PRICE"):
        open_time = first_open_ms
        while open_time <= last_open_ms:
            fields = {"open": price, "high": price, "low": price, "close": price}
            fields.update(overrides.get(open_time, {}))
            bars.append(OHLCVBar(
                symbol=symbol, interval="1m", price_type=price_type, open_time=open_time,
                base_vol=1.0, quote_vol=1.0, **fields,
            ))
            open_time += MINUTE_MS
    return bars


def flat_closes(n: int, price: float, *, tail: int = 0) -> list[float]:
    """Precio casi plano con un vaiven minimo (nunca perfectamente plano: los indicadores que
    dividen, como el RSI, no deben encontrar varianza cero), terminando en `tail` cierres
    IDENTICOS a `price`. El tramo final constante importa: con un vaiven alterno hasta la ultima
    vela las EMA 9/21 cruzan por azar segun la paridad de `n` y `ema_cross_9_21` dispara solo."""
    wiggle = [price * (1.0 + (0.0001 if i % 2 == 0 else -0.0001)) for i in range(n - tail)]
    return wiggle + [price] * tail


def neutral_4h_closes(price: float) -> list[float]:
    """Serie de 4 h en la que NINGUNA estrategia de precio dispara en la ultima vela."""
    return flat_closes(BARS_4H, price, tail=30)


def _frame(closes: list[float]) -> pd.DataFrame:
    """El mismo DataFrame que arma el generador de senales a partir de las velas de la cache."""
    bars = bars_from_closes(closes, symbol="X", interval="4h", end_ms=10**12)
    frame = pd.DataFrame([b.model_dump() for b in bars]).sort_values("open_time")
    return frame.reset_index(drop=True)


def signals_on_last_bar(closes: list[float]) -> dict[str, Signal]:
    """Lo que dicen las estrategias reales (4 h, sin funding) sobre la ultima vela de la serie."""
    df = _frame(closes)
    out = {}
    for name in PRICE_STRATEGIES_4H:
        strategy = STRATEGIES[name]
        out[name] = strategy.evaluate(strategy.precompute(df))
    return out


def _path(returns: list[float], base: float) -> list[float]:
    """Precios que terminan en `base` tras aplicar `returns` (en escala logaritmica)."""
    log_prices = [0.0]
    for r in returns:
        log_prices.append(log_prices[-1] + math.log1p(r))
    shift = math.log(base) - log_prices[-1]
    return [math.exp(p + shift) for p in log_prices[1:]]


def _trend_cross(
    sign: float, pullback_bars: int, pullback: float, rebound_steps: int, base: float,
) -> list[float]:
    """Tendencia a favor (+0.15 %/vela si `sign` > 0), retroceso corto y un rebote final."""
    returns = (
        [sign * 0.0015] * (BARS_4H - pullback_bars - 1)
        + [-sign * pullback] * pullback_bars
        + [sign * rebound_steps * 0.0005]
    )
    return _path(returns, base)


@functools.cache
def find_trend_cross_closes(direction: Side, base: float = 100.0) -> tuple[float, ...]:
    """Serie de 4 h que termina (en `base`) con un cruce EMA 9/21 `direction` a favor de la
    tendencia mayor: disparan `ema_cross_9_21` y `trend_atr_stop_9_21_50` (que trae SL y trailing
    por ATR) pero NO `donchian_breakout_20`. La estrategia representante de la senal la decide la
    lista de elegibles de la cuenta, asi un mismo escenario sirve para SL/TP por porcentaje
    (`ema_cross_9_21`) o con trailing real (`trend_atr_stop_9_21_50`)."""
    sign = 1.0 if direction == Side.LONG else -1.0
    wanted = Signal.LONG if direction == Side.LONG else Signal.SHORT
    candidates = [_KNOWN_TREND_PARAMS[direction]]
    candidates += [
        (bars, step, rebound)
        for bars in (6, 3, 4, 5, 7, 8, 2)
        for step in (0.005, 0.002, 0.003, 0.004, 0.0035, 0.0025, 0.006, 0.008)
        for rebound in range(1, 41)
    ]
    for pullback_bars, pullback, rebound_steps in candidates:
        closes = _trend_cross(sign, pullback_bars, pullback, rebound_steps, base)
        signals = signals_on_last_bar(closes)
        if (
            signals["ema_cross_9_21"] == wanted
            and signals["trend_atr_stop_9_21_50"] == wanted
            and signals["donchian_breakout_20"] == Signal.HOLD
        ):
            return tuple(closes)
    raise AssertionError(
        f"no existe una serie 4h de cruce {direction.value} con tendencia sin ruptura Donchian"
    )
