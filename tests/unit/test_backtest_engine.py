"""Tests del motor de backtest (app/backtesting/engine.py) -- casos limite
criticos exigidos por CLAUDE.md para funciones de riesgo/simulacion: peor
caso SL-antes-que-TP en la misma vela, liquidacion por MARK_PRICE distinta
de SL por LAST_PRICE, orden SL-vs-liquidacion por cercania de precio,
ejecucion al OPEN cuando hay gap, evaluacion de la propia vela de entrada,
tope de riesgo por operacion, cierre forzado al fin de los datos, y
ausencia de sesgo de anticipacion.

**Correccion post-Fase-2**: el motor ya no descarga nada por red (lee solo
de `ohlcv_cache`/`funding_cache`), asi que estos tests siembran la base de
datos directamente con `ohlcv_repo.upsert_bars` en vez de un cliente REST
falso."""

from __future__ import annotations

import pytest

from app.backtesting.engine import run_backtest
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


def _flat_series(n: int, price: float, price_type: str) -> list[OHLCVBar]:
    return [_bar(i, price, price * 1.001, price * 0.999, price, price_type) for i in range(n)]


async def _seed(db, last: list[OHLCVBar], mark: list[OHLCVBar]) -> None:
    await ohlcv_repo.upsert_bars(db, last)
    await ohlcv_repo.upsert_bars(db, mark)


class FakeStrategy(BaseStrategy):
    """Estrategia de prueba con comportamiento totalmente controlado: emite
    la senal configurada exactamente en el indice `signal_at` (cuando
    `evaluate` recibe un sub-dataframe de longitud `signal_at + 1`)."""

    def __init__(self, signal_at: int, side: Signal, sl=None, tp=None, trailing=None) -> None:
        self.name = "fake"
        self.signal_at = signal_at
        self.side = side
        self._sl = sl
        self._tp = tp
        self._trailing = trailing

    def evaluate(self, df) -> Signal:
        idx = len(df) - 1
        return self.side if idx == self.signal_at else Signal.HOLD

    def stop_price(self, df, side, entry_price):
        return self._sl

    def take_profit_price(self, df, side, entry_price):
        return self._tp

    def trailing_distance(self, df):
        return self._trailing


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006,
        MAX_SL_MARGIN_LOSS_PCT=50.0, BACKTEST_SLIPPAGE_BPS=0.0,
        BACKTEST_FALLBACK_SL_PCT=0.05, BACKTEST_FALLBACK_TP_PCT=0.10,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


N_PAD = 60  # suficiente para superar el minimo de 60 velas alineadas del motor
# `now_ms` fijo bien por delante de todas las fixtures, para que
# `drop_incomplete_last_bar` nunca descarte la ultima vela de prueba.
NOW_MS = BASE_MS + 10_000 * STEP_MS


@pytest.mark.asyncio
async def test_sl_wins_over_tp_in_same_bar_worst_case(db):
    """Vela de prueba toca tanto el SL (95) como el TP (110) a la vez:
    debe ganar el SL (regla explicita del usuario, peor caso)."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    last.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "LAST_PRICE"))
    last.append(_bar(N_PAD + 1, 100.0, 112.0, 90.0, 100.0, "LAST_PRICE"))
    mark.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    mark.append(_bar(N_PAD + 1, 100.0, 105.0, 96.0, 100.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings()
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=95.0, tp=110.0)

    trades, skipped = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert skipped == []
    assert len(trades) == 1
    assert trades[0].close_reason == "SL"
    assert trades[0].exit_price == pytest.approx(95.0)


@pytest.mark.asyncio
async def test_liquidation_uses_mark_price_independent_of_last_price(db):
    """SL y TP muy anchos (no se tocan); el precio MARK cae lo bastante
    para liquidar aunque el precio LAST (usado para SL/TP) no lo refleje."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    last.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "LAST_PRICE"))
    last.append(_bar(N_PAD + 1, 100.0, 105.0, 95.0, 100.0, "LAST_PRICE"))  # no toca SL/TP anchos
    mark.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    mark.append(_bar(N_PAD + 1, 100.0, 101.0, 85.0, 100.0, "MARK_PRICE"))  # rompe liquidacion
    await _seed(db, last, mark)

    # SL ancho adrede, no es lo que se testea aqui.
    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=50.0, tp=200.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "LIQUIDATION"


@pytest.mark.asyncio
async def test_sl_triggers_before_liquidation_when_sl_is_closer(db):
    """Tarea 4b: el SL (95, mas cerca del entry 100) debe ganarle a la
    liquidacion (~90.5) aunque la vela toque ambos niveles -- el precio
    llega primero al SL al moverse en contra."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    last.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "LAST_PRICE"))
    # Cae hasta 85: cruza tanto el SL=95 como la liquidacion (~90.5).
    last.append(_bar(N_PAD + 1, 100.0, 100.5, 85.0, 95.0, "LAST_PRICE"))
    mark.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    mark.append(_bar(N_PAD + 1, 100.0, 100.5, 85.0, 95.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=95.0, tp=200.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "SL"
    assert trades[0].exit_price == pytest.approx(95.0)


@pytest.mark.asyncio
async def test_liquidation_triggers_before_sl_when_liquidation_is_closer(db):
    """Simetrico: si el SL (muy ancho, 50) queda mas lejos del entry que la
    liquidacion (~90.5), la liquidacion debe ganar -- el precio la alcanza
    primero al moverse en contra."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    last.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "LAST_PRICE"))
    last.append(_bar(N_PAD + 1, 100.0, 100.5, 85.0, 90.0, "LAST_PRICE"))
    mark.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    mark.append(_bar(N_PAD + 1, 100.0, 100.5, 85.0, 90.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=50.0, tp=200.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "LIQUIDATION"


@pytest.mark.asyncio
async def test_gap_below_stop_fills_at_open_not_at_stop_price(db):
    """Tarea 4c: si la vela ABRE ya por debajo del SL (gap bajista), el
    cierre debe ejecutarse al OPEN de esa vela, no al precio nominal del
    SL (que nunca se transo)."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    last.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "LAST_PRICE"))
    # Vela siguiente abre en 80 (muy por debajo del SL=95) -- gap.
    last.append(_bar(N_PAD + 1, 80.0, 81.0, 79.0, 80.0, "LAST_PRICE"))
    mark.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    mark.append(_bar(N_PAD + 1, 80.0, 81.0, 79.0, 80.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=95.0, tp=200.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "SL"
    assert trades[0].exit_price == pytest.approx(80.0)  # el open del gap, no 95.0


@pytest.mark.asyncio
async def test_entry_bar_itself_can_trigger_sl(db):
    """Tarea 4a: la propia vela de entrada (donde se abre al OPEN) tambien
    debe evaluarse contra el SL -- si su rango lo toca, cierra en esa misma
    vela en vez de esperar a la siguiente."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    # Vela de entrada: abre en 100 (fill) pero cae hasta 94 en el mismo rango.
    last.append(_bar(N_PAD, 100.0, 100.5, 94.0, 96.0, "LAST_PRICE"))
    mark.append(_bar(N_PAD, 100.0, 100.5, 94.0, 96.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=95.0, tp=200.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 1) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "SL"
    assert trades[0].exit_price == pytest.approx(95.0)
    assert trades[0].entry_time == trades[0].exit_time  # cerro en su propia vela de entrada


@pytest.mark.asyncio
async def test_entry_fills_at_next_bar_open_not_signal_bar_close(db):
    """Verifica la ausencia de sesgo de anticipacion: el precio de entrada
    debe ser el OPEN de la vela siguiente a la senal, no el CLOSE de la
    vela de la senal."""
    last = _flat_series(N_PAD - 1, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD - 1, 100.0, "MARK_PRICE")
    # close extremo en la vela de senal (open muy distinto del close anterior)
    last.append(_bar(N_PAD - 1, 100.0, 100.1, 99.9, 999.0, "LAST_PRICE"))
    mark.append(_bar(N_PAD - 1, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    for i in range(N_PAD, N_PAD + 5):
        last.append(_bar(i, 50.0, 50.1, 49.9, 50.0, "LAST_PRICE"))
        mark.append(_bar(i, 50.0, 50.1, 49.9, 50.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=10.0, tp=500.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 5) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].entry_price == pytest.approx(50.0)  # open de la vela siguiente, no 999.0


@pytest.mark.asyncio
async def test_risk_cap_skips_entry_when_sl_too_wide(db):
    """SL implicaria perder mas del tope configurado del margen a 10x: la
    entrada se omite y queda registrada, no se abre ninguna operacion."""
    last = _flat_series(N_PAD + 2, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD + 2, 100.0, "MARK_PRICE")
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=50.0)
    # sl a 70 de un entry ~100 -> distancia 30% * leverage 10x = 300% de
    # margen, muy por encima del tope.
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=70.0, tp=200.0)

    trades, skipped = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert trades == []
    assert len(skipped) == 1
    assert skipped[0].intended_sl_margin_loss_pct == pytest.approx(300.0, rel=0.05)


@pytest.mark.asyncio
async def test_open_position_force_closed_at_end_of_data(db):
    """Si los datos terminan con una posicion abierta (sin tocar SL/TP/
    liquidacion), se cierra al close de la ultima vela con motivo
    END_OF_DATA."""
    last = _flat_series(N_PAD + 2, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD + 2, 100.0, "MARK_PRICE")
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=10.0, tp=1000.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "END_OF_DATA"
    assert trades[0].exit_price == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_trailing_stop_ratchets_up_and_triggers_on_retracement(db):
    """El trailing stop sube con el maximo favorable y dispara en una vela
    POSTERIOR cuando el precio retrocede lo suficiente (nunca en la misma
    vela en que se actualizo, ver docs/FASE2_PLAN.md)."""
    last = _flat_series(N_PAD, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD, 100.0, "MARK_PRICE")
    last.append(_bar(N_PAD, 100.0, 100.1, 99.9, 100.0, "LAST_PRICE"))  # fill bar
    last.append(_bar(N_PAD + 1, 100.0, 110.0, 99.9, 110.0, "LAST_PRICE"))  # favorable: sube a 110
    last.append(_bar(N_PAD + 2, 108.0, 108.0, 104.0, 106.0, "LAST_PRICE"))  # retrocede, toca trail.
    for i in range(N_PAD, N_PAD + 3):
        mark.append(_bar(i, 100.0, 100.1, 99.9, 100.0, "MARK_PRICE"))
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=80.0, tp=None, trailing=5.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 3) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].close_reason == "TRAILING"
    assert trades[0].exit_price == pytest.approx(105.0)  # 110 (mejor precio) - 5 (distancia)


@pytest.mark.asyncio
async def test_funding_flag_is_approximated_when_no_real_events(db):
    """Sin eventos reales de funding cacheados, toda operacion debe quedar
    marcada como funding aproximado."""
    last = _flat_series(N_PAD + 2, 100.0, "LAST_PRICE")
    mark = _flat_series(N_PAD + 2, 100.0, "MARK_PRICE")
    await _seed(db, last, mark)

    settings = make_settings(MAX_SL_MARGIN_LOSS_PCT=1000.0)
    strategy = FakeStrategy(signal_at=N_PAD - 1, side=Signal.LONG, sl=10.0, tp=1000.0)

    trades, _ = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (N_PAD + 2) * STEP_MS, settings, now_ms=NOW_MS,
    )

    assert len(trades) == 1
    assert trades[0].funding_is_approximated is True
    assert trades[0].funding_paid_usdt == pytest.approx(0.0)  # tasa aproximada = 0 sin eventos


@pytest.mark.asyncio
async def test_too_few_bars_returns_no_trades(db):
    last = _flat_series(10, 100.0, "LAST_PRICE")
    mark = _flat_series(10, 100.0, "MARK_PRICE")
    await _seed(db, last, mark)

    settings = make_settings()
    strategy = FakeStrategy(signal_at=5, side=Signal.LONG)

    trades, skipped = await run_backtest(
        db, strategy, "fake", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + 10 * STEP_MS, settings, now_ms=NOW_MS,
    )
    assert trades == []
    assert skipped == []
