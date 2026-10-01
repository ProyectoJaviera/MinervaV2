"""Pruebas de sanidad de extremo a extremo del motor de backtest (tarea 6):
casos donde se conoce de antemano cual debe ser el resultado cualitativo,
independientemente de los detalles de una estrategia puntual.

1. Tendencia clara + estrategia trivial que debe ganar.
2. Camino aleatorio (sin sesgo): el profit factor NETO debe quedar por
   debajo de 1 -- sin ninguna ventaja estructural, los costos (comisiones)
   garantizan una perdida neta aunque el resultado bruto ronde el empate.
3. SL y liquidacion en la misma vela: cubierto por
   `test_sl_triggers_before_liquidation_when_sl_is_closer` y
   `test_liquidation_triggers_before_sl_when_liquidation_is_closer` en
   `test_backtest_engine.py` (orden por cercania de precio, tarea 4b).
"""

from __future__ import annotations

import random

import pytest

from app.backtesting.engine import run_backtest
from app.backtesting.metrics import profit_factor_only
from app.config import Settings
from app.market.ohlcv_history import interval_to_ms
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo
from app.strategies.base import BaseStrategy, Signal

BASE_MS = 1_700_000_000_000
STEP_MS = interval_to_ms("4h")


def _bar(idx: int, o: float, h: float, low: float, c: float, price_type: str) -> OHLCVBar:
    return OHLCVBar(
        symbol="BTCUSDT", interval="4h", price_type=price_type,
        open_time=BASE_MS + idx * STEP_MS, open=o, high=h, low=low, close=c,
    )


async def _seed(db, last: list[OHLCVBar], mark: list[OHLCVBar]) -> None:
    await ohlcv_repo.upsert_bars(db, last)
    await ohlcv_repo.upsert_bars(db, mark)


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006,
        MAX_SL_MARGIN_LOSS_PCT=1000.0, BACKTEST_SLIPPAGE_BPS=0.0,
        BACKTEST_FALLBACK_SL_PCT=0.05, BACKTEST_FALLBACK_TP_PCT=0.10,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


class BuyAndHoldOnce(BaseStrategy):
    """Estrategia trivial: entra LONG una sola vez, en el indice `entry_at`,
    y nunca mas emite senal (se mantiene hasta fin de datos/SL/TP)."""

    def __init__(self, entry_at: int) -> None:
        self.name = "buy_and_hold_once"
        self.entry_at = entry_at

    def evaluate(self, df) -> Signal:
        return Signal.LONG if len(df) - 1 == self.entry_at else Signal.HOLD


@pytest.mark.asyncio
async def test_clear_uptrend_with_trivial_strategy_is_profitable(db):
    """Tendencia clara (precio sube de forma monotona y suave) + una
    estrategia trivial que compra una vez y sostiene: el resultado debe
    ser claramente ganador (bruto y neto), SL/TP lo bastante anchos para
    no interferir."""
    n = 100
    last, mark = [], []
    price = 100.0
    for i in range(n):
        last.append(_bar(i, price, price * 1.001, price * 0.999, price, "LAST_PRICE"))
        mark.append(_bar(i, price, price * 1.001, price * 0.999, price, "MARK_PRICE"))
        price *= 1.01  # +1% por vela, tendencia alcista suave y constante
    await _seed(db, last, mark)

    # TP/SL de respaldo bien anchos: lo que se testea es que la tendencia
    # por si sola gana, no una salida puntual por TP.
    settings = make_settings(BACKTEST_FALLBACK_TP_PCT=100.0, BACKTEST_FALLBACK_SL_PCT=0.99)
    strategy = BuyAndHoldOnce(entry_at=4)
    now_ms = BASE_MS + (n + 100) * STEP_MS

    trades, _ = await run_backtest(
        db, strategy, "buy_and_hold_once", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (n - 1) * STEP_MS, settings, now_ms=now_ms,
    )

    assert len(trades) == 1
    assert trades[0].pnl_gross_usdt > 0
    assert trades[0].pnl_net_usdt > 0
    assert trades[0].close_reason == "END_OF_DATA"


class AlwaysReenterSymmetric(BaseStrategy):
    """Siempre entra LONG en cuanto queda plana, con SL y TP simetricos
    (misma distancia porcentual) alrededor del precio de entrada -- sin
    ninguna ventaja direccional. Sobre un camino aleatorio sin sesgo, el
    resultado BRUTO de cada operacion es, en esencia, una moneda justa; el
    costo fijo (comisiones) se paga siempre, se gane o se pierda."""

    def __init__(self, sl_tp_pct: float) -> None:
        self.name = "always_reenter_symmetric"
        self.sl_tp_pct = sl_tp_pct

    def evaluate(self, df) -> Signal:
        return Signal.LONG

    def stop_price(self, df, side, entry_price):
        return entry_price * (1 - self.sl_tp_pct)

    def take_profit_price(self, df, side, entry_price):
        return entry_price * (1 + self.sl_tp_pct)


@pytest.mark.asyncio
async def test_random_walk_has_net_profit_factor_below_one_due_to_costs(db):
    """Camino aleatorio sin sesgo (retornos de media ~0): una estrategia sin
    ninguna ventaja direccional (SL/TP simetricos, siempre re-entra) sobre
    ruido puro produce un resultado BRUTO cercano al empate (a veces
    positivo, a veces negativo, segun la semilla) -- pero el profit factor
    NETO (que resta comisiones en cada entrada/salida, cientos de veces)
    debe quedar por debajo de 1 de forma consistente, sea cual sea la
    semilla. Se verifica con 5 semillas fijas (deterministico)."""
    for seed in (1, 2, 3, 7, 42):
        rng = random.Random(seed)
        n = 1500
        last, mark = [], []
        price = 100.0
        for i in range(n):
            o = price
            h = price * 1.006
            low = price * 0.994
            c = price * (1 + rng.gauss(0.0, 0.004))  # retorno medio ~0, sin sesgo
            last.append(_bar(i, o, h, low, c, "LAST_PRICE"))
            mark.append(_bar(i, o, h, low, c, "MARK_PRICE"))
            price = c
        await _seed(db, last, mark)

        settings = make_settings()
        strategy = AlwaysReenterSymmetric(sl_tp_pct=0.01)
        now_ms = BASE_MS + (n + 100) * STEP_MS

        trades, _ = await run_backtest(
            db, strategy, "always_reenter_symmetric", "BTCUSDT", "4h",
            BASE_MS, BASE_MS + (n - 1) * STEP_MS, settings, now_ms=now_ms,
        )

        pf = profit_factor_only(trades)
        gross_total = sum(t.pnl_gross_usdt for t in trades)
        net_total = sum(t.pnl_net_usdt for t in trades)

        assert len(trades) >= 100, f"seed={seed}: muy pocas operaciones ({len(trades)})"
        assert net_total < gross_total, f"seed={seed}: las comisiones siempre restan"
        assert pf is not None and pf < 1.0, f"seed={seed}: PF neto {pf} no quedo por debajo de 1"
