"""Features de mercado para el prompt del LLM (subfase 3.6, fase iv): ATR,
volatilidad, correlacion con BTC, funding y posiciones reales abiertas, todo
leido de la cache local -- y las 3 pruebas de fuga obligatorias de
docs/FASE3_6_LLM.md seccion (a)."""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime, timedelta

import pytest

from app.llm import market_features as mf
from app.persistence.models import ContractSpec, OHLCVBar, Side, Trade, TradeStatus
from app.persistence.repositories import funding_repo, ohlcv_repo, specs_repo, trades_repo

HOUR_MS = 3_600_000
BASE_MS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def make_bars(symbol: str, closes: list[float], start_ms: int = BASE_MS,
              step_ms: int = HOUR_MS, interval: str = "1h") -> list[OHLCVBar]:
    bars = []
    prev_close = closes[0] * 0.999
    for i, close in enumerate(closes):
        open_time = start_ms + i * step_ms
        high = max(close, prev_close) * 1.001
        low = min(close, prev_close) * 0.999
        bars.append(OHLCVBar(
            symbol=symbol, interval=interval, price_type="LAST_PRICE", open_time=open_time,
            open=prev_close, high=high, low=low, close=close,
        ))
        prev_close = close
    return bars


def rising_closes(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


async def seed(db, symbol: str, closes: list[float], **kwargs) -> int:
    """Siembra `len(closes)` velas 1h para `symbol` y devuelve el `open_time`
    (ms) de la ULTIMA, que se usa como `as_of_ms` (cierre de la vela evaluada
    = esa vela YA cerro, es la mas reciente disponible al decidir)."""
    bars = make_bars(symbol, closes, **kwargs)
    await ohlcv_repo.upsert_bars(db, bars)
    return bars[-1].open_time + HOUR_MS  # as_of = cuando cierra la ultima vela


# --- fetch_closed_candles: limite de instante ----------------------------------


@pytest.mark.asyncio
async def test_fetch_closed_candles_excludes_a_bar_that_has_not_closed_yet(db):
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(20))
    bars = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, limit=10)
    assert len(bars) == 10
    assert all(b.open_time + HOUR_MS <= as_of_ms for b in bars)


@pytest.mark.asyncio
async def test_fetch_closed_candles_returns_fewer_when_not_enough_cached(db):
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(5))
    bars = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, limit=20)
    assert len(bars) == 5  # no lanza, no inventa: devuelve lo que hay


# --- ATR y desviacion de retornos -----------------------------------------------


def test_atr14_pct_is_none_with_fewer_than_fifteen_bars():
    bars = make_bars("BTCUSDT", rising_closes(14))
    assert mf.compute_atr14_pct(bars) is None


def test_atr14_pct_is_a_small_positive_percentage_for_calm_prices():
    bars = make_bars("BTCUSDT", rising_closes(20, start=100.0, step=0.1))
    atr = mf.compute_atr14_pct(bars)
    assert atr is not None and 0.0 < atr < 1.0  # porcentaje, no fraccion


def test_atr14_pct_is_actually_in_percent_not_a_raw_fraction():
    """Revision del piloto v2 (docs/FASE3_6_LLM.md seccion k): el modelo leyo
    el ATR como si ya fuera porcentaje cuando en realidad era la fraccion
    cruda, ~100 veces mas chica. Serie conocida: reconstruye el ATR(14) a
    mano en FRACCION (formula original, antes del fix) y confirma que la
    funcion devuelve exactamente esa fraccion * 100."""
    bars = make_bars("BTCUSDT", rising_closes(20, start=100.0, step=0.1))
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr_fraction = sum(true_ranges[-14:]) / 14 / bars[-1].close

    atr_pct = mf.compute_atr14_pct(bars)

    assert atr_pct == pytest.approx(atr_fraction * 100)


def test_returns_std_pct_is_none_with_fewer_than_required_bars():
    bars = make_bars("BTCUSDT", rising_closes(10))
    assert mf.compute_returns_std_pct(bars, n=30) is None


def test_returns_std_pct_is_zero_for_a_perfectly_steady_percentage_move():
    closes = [100.0 * (1.01 ** i) for i in range(32)]  # mismo retorno cada vela
    bars = make_bars("BTCUSDT", closes)
    std = mf.compute_returns_std_pct(bars, n=30)
    assert std is not None and std == pytest.approx(0.0, abs=1e-9)


def test_returns_std_pct_is_actually_in_percent_not_a_raw_fraction():
    """Misma revision que el ATR: antes `returns_std_30` (sin sufijo `_pct`)
    devolvia la desviacion de los log-retornos cruda; ahora
    `returns_std_30_pct` es esa misma fraccion * 100."""
    closes = [100.0 + i * 0.37 for i in range(35)]  # variacion no perfectamente lineal
    bars = make_bars("BTCUSDT", closes)
    raw_closes = [b.close for b in bars[-31:]]
    returns = [
        math.log(raw_closes[i] / raw_closes[i - 1]) for i in range(1, len(raw_closes))
    ]
    std_fraction = statistics.stdev(returns)

    std_pct = mf.compute_returns_std_pct(bars, n=30)

    assert std_pct == pytest.approx(std_fraction * 100)


# --- contexto de tendencia en 1h (revision del piloto v1) -----------------------


def test_return_pct_is_none_without_enough_bars():
    bars = make_bars("BTCUSDT", rising_closes(24))
    assert mf.compute_return_pct(bars, bars_back=24) is None  # hacen falta 25


def test_return_pct_matches_the_simple_return_between_the_two_closes():
    bars = make_bars("BTCUSDT", rising_closes(25, start=100.0, step=1.0))
    # Cierre de hace 24 velas: 100.0; ultimo cierre: 124.0.
    pct = mf.compute_return_pct(bars, bars_back=24)
    assert pct == pytest.approx((124.0 - 100.0) / 100.0 * 100)


def test_return_pct_is_negative_for_falling_prices():
    bars = make_bars("BTCUSDT", [200.0 - i for i in range(25)])
    pct = mf.compute_return_pct(bars, bars_back=24)
    assert pct is not None and pct < 0


def test_ema_distance_pct_is_none_with_fewer_than_the_period_bars():
    bars = make_bars("BTCUSDT", rising_closes(49))
    assert mf.compute_ema_distance_pct(bars, period=50) is None


def test_ema_distance_pct_is_positive_when_price_has_been_rising():
    bars = make_bars("BTCUSDT", rising_closes(60, start=100.0, step=1.0))
    distance = mf.compute_ema_distance_pct(bars, period=50)
    assert distance is not None and distance > 0  # el cierre va por encima de la EMA


def test_ema_distance_pct_is_zero_for_a_perfectly_flat_series():
    bars = make_bars("BTCUSDT", [100.0] * 60)
    distance = mf.compute_ema_distance_pct(bars, period=50)
    assert distance == pytest.approx(0.0, abs=1e-9)


def test_compute_trend_features_bundles_the_three_fields():
    bars = make_bars("BTCUSDT", rising_closes(80, start=100.0, step=1.0))
    trend = mf.compute_trend_features(bars)
    assert set(trend) == {"return_24h_pct", "return_72h_pct", "distance_to_ema50_1h_pct"}
    assert trend["return_24h_pct"] is not None  # hacen falta 25, hay 80
    assert trend["return_72h_pct"] is not None  # hacen falta 73, hay 80
    assert trend["distance_to_ema50_1h_pct"] is not None  # hacen falta 50, hay 80


def test_compute_trend_features_return_72h_is_null_with_fewer_than_73_bars():
    bars = make_bars("BTCUSDT", rising_closes(72, start=100.0, step=1.0))
    trend = mf.compute_trend_features(bars)
    assert trend["return_24h_pct"] is not None
    assert trend["return_72h_pct"] is None  # hacen falta 73, hay solo 72


# --- correlacion con BTC ---------------------------------------------------------


@pytest.mark.asyncio
async def test_btc_correlation_is_high_for_two_series_moving_together(db):
    closes = rising_closes(35, start=100.0, step=1.0)
    as_of_ms = await seed(db, "BTCUSDT", closes)
    await seed(db, "ETHUSDT", [c * 10 for c in closes])  # mismos retornos, otra escala

    corr = await mf.compute_btc_correlation(db, "ETHUSDT", as_of_ms, n=30)

    assert corr is not None and corr > 0.99


@pytest.mark.asyncio
async def test_btc_correlation_is_none_without_enough_aligned_candles(db):
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(35))
    await seed(db, "ETHUSDT", rising_closes(5))  # muchas menos velas alineadas

    corr = await mf.compute_btc_correlation(db, "ETHUSDT", as_of_ms, n=30)

    assert corr is None


# --- funding ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_funding_features_use_the_latest_event_at_or_before_the_candle(db):
    await specs_repo.upsert_spec(db, ContractSpec(
        symbol="BTCUSDT", funding_interval_hours=8, fetched_at=datetime.now(UTC),
    ))
    await funding_repo.upsert_funding(db, "BTCUSDT", [
        (BASE_MS, 0.0001), (BASE_MS + 8 * HOUR_MS, 0.0002),
    ])
    candle_close_ms = BASE_MS + 9 * HOUR_MS  # 1h despues del segundo evento

    features = await mf.compute_funding_features(db, "BTCUSDT", candle_close_ms, 9.0)

    # funding_rate_pct esta en PORCENTAJE (0,0002 de fraccion -> 0,02 de %),
    # igual que signals.funding_rate_pct (signal_generator.py, `* 100`).
    assert features["funding_rate_pct"] == pytest.approx(0.02)
    assert features["funding_interval_hours"] == 8.0
    assert features["funding_is_approximated"] is False  # dentro de 8h + 9h de margen


@pytest.mark.asyncio
async def test_funding_is_approximated_when_the_latest_event_is_stale(db):
    await specs_repo.upsert_spec(db, ContractSpec(
        symbol="BTCUSDT", funding_interval_hours=8, fetched_at=datetime.now(UTC),
    ))
    await funding_repo.upsert_funding(db, "BTCUSDT", [(BASE_MS, 0.0001)])
    candle_close_ms = BASE_MS + 100 * HOUR_MS  # mucho despues del unico evento

    features = await mf.compute_funding_features(db, "BTCUSDT", candle_close_ms, 9.0)

    assert features["funding_rate_pct"] == pytest.approx(0.01)
    assert features["funding_is_approximated"] is True


@pytest.mark.asyncio
async def test_funding_features_are_null_without_any_cached_event(db):
    features = await mf.compute_funding_features(db, "BTCUSDT", BASE_MS, 9.0)
    assert features == {
        "funding_rate_pct": None, "funding_interval_hours": mf.DEFAULT_FUNDING_INTERVAL_HOURS,
        "funding_is_approximated": None,
    }


@pytest.mark.asyncio
async def test_funding_never_uses_an_event_after_the_evaluated_candle(db):
    await funding_repo.upsert_funding(db, "BTCUSDT", [
        (BASE_MS, 0.0001), (BASE_MS + 100 * HOUR_MS, 0.9999),  # muy posterior
    ])
    features = await mf.compute_funding_features(db, "BTCUSDT", BASE_MS + HOUR_MS, 9.0)
    assert features["funding_rate_pct"] == pytest.approx(0.01)  # nunca ve el evento futuro


@pytest.mark.asyncio
async def test_funding_rate_pct_is_in_percent_not_the_raw_cache_fraction(db):
    # Unidades (error real encontrado y corregido en la fase iv): `funding_cache`
    # guarda la fraccion cruda de Bitunix (0,0001 = 0,01 %); la feature del
    # prompt tiene que devolver PORCENTAJE (0,01), no la fraccion sin convertir.
    await funding_repo.upsert_funding(db, "BTCUSDT", [(BASE_MS, 0.0001)])
    features = await mf.compute_funding_features(db, "BTCUSDT", BASE_MS, 9.0)
    assert features["funding_rate_pct"] == pytest.approx(0.01)  # no 0.0001


# --- posiciones reales abiertas (nunca sombras) ----------------------------------


async def _open_trade(db, symbol, side, opened_h, closed_h=None) -> Trade:
    trade = Trade(
        symbol=symbol, side=side, strategy=None, status=TradeStatus.OPEN, leverage=10,
        margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0, fee_entry_usdt=0.0,
        opened_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=opened_h),
    )
    created = await trades_repo.create_trade(db, trade)
    if closed_h is not None:
        await trades_repo.close_trade(
            db, created.id, exit_price=100.0, fee_exit_usdt=0.0, pnl_gross_usdt=0.0,
            pnl_net_usdt=0.0, close_reason="MANUAL",
            closed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=closed_h),
        )
    return created


@pytest.mark.asyncio
async def test_open_real_positions_counts_only_what_was_open_at_that_instant(db):
    as_of = datetime(2026, 1, 1, 5, tzinfo=UTC)
    await _open_trade(db, "BTCUSDT", Side.LONG, opened_h=0)  # sigue abierta
    await _open_trade(db, "ETHUSDT", Side.SHORT, opened_h=1, closed_h=10)  # abierta en as_of
    await _open_trade(db, "SOLUSDT", Side.LONG, opened_h=1, closed_h=2)  # ya cerrada antes
    await _open_trade(db, "XRPUSDT", Side.LONG, opened_h=6)  # se abre DESPUES de as_of

    positions = await mf.compute_open_real_positions(db, "BTCUSDT", as_of)

    assert positions["open_real_positions_total"] == 2
    assert positions["open_real_positions_same_symbol"] == 1
    assert positions["open_real_positions_long"] == 1
    assert positions["open_real_positions_short"] == 1


# --- las 3 pruebas de fuga obligatorias (seccion a) ------------------------------


@pytest.mark.asyncio
async def test_leakage_future_candles_with_extreme_values_do_not_change_the_features(db):
    # 80 velas: alcanza para que las 3 features de tendencia sean no-nulas
    # (necesitan 25/50/73), no solo las 40 que bastaban para ATR/std.
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(80))
    bars_before = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 80)
    atr_before = mf.compute_atr14_pct(bars_before)
    std_before = mf.compute_returns_std_pct(bars_before, n=30)
    trend_before = mf.compute_trend_features(bars_before)
    assert all(v is not None for v in trend_before.values())  # si no, no probaria nada

    # Velas futuras con valores extremos, DESPUES de as_of_ms.
    extreme_future = [
        OHLCVBar(symbol="BTCUSDT", interval="1h", price_type="LAST_PRICE",
                 open_time=as_of_ms + i * HOUR_MS, open=1e9, high=1e9, low=1.0, close=1e9)
        for i in range(5)
    ]
    await ohlcv_repo.upsert_bars(db, extreme_future)

    bars_after = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 80)
    atr_after = mf.compute_atr14_pct(bars_after)
    std_after = mf.compute_returns_std_pct(bars_after, n=30)
    trend_after = mf.compute_trend_features(bars_after)

    assert atr_after == atr_before
    assert std_after == std_before
    assert trend_after == trend_before


@pytest.mark.asyncio
async def test_leakage_full_series_vs_truncated_at_close_give_the_same_result(db):
    closes = rising_closes(40)
    as_of_ms = BASE_MS + 29 * HOUR_MS + HOUR_MS  # cierre de la vela #29 (0-indexada)

    # Base "truncada": solo las velas hasta as_of_ms existen.
    await ohlcv_repo.upsert_bars(db, make_bars("BTCUSDT", closes[:30]))
    truncated_bars = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 30)
    truncated_atr = mf.compute_atr14_pct(truncated_bars)
    truncated_trend = mf.compute_trend_features(truncated_bars)

    # Misma base, pero ahora con el resto de la serie "completa" (el futuro real).
    await ohlcv_repo.upsert_bars(db, make_bars("BTCUSDT", closes))
    full_bars_fetched_as_of_the_same_close = await mf.fetch_closed_candles(
        db, "BTCUSDT", as_of_ms, 30
    )
    full_atr_as_of_the_same_close = mf.compute_atr14_pct(full_bars_fetched_as_of_the_same_close)
    full_trend_as_of_the_same_close = mf.compute_trend_features(
        full_bars_fetched_as_of_the_same_close
    )

    assert [b.open_time for b in truncated_bars] == [
        b.open_time for b in full_bars_fetched_as_of_the_same_close
    ]
    assert truncated_atr == full_atr_as_of_the_same_close
    assert truncated_trend == full_trend_as_of_the_same_close


@pytest.mark.asyncio
async def test_leakage_no_feature_ever_uses_a_bar_with_open_time_after_the_close(db):
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(40))
    await ohlcv_repo.upsert_bars(db, make_bars(
        "BTCUSDT", rising_closes(5), start_ms=as_of_ms + HOUR_MS,
    ))

    bars = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, limit=100)

    assert all(b.open_time <= as_of_ms for b in bars)
    assert all(b.open_time + HOUR_MS <= as_of_ms for b in bars)  # mas estricto: vela ya cerrada
