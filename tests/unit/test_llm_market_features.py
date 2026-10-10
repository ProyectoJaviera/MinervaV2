"""Features de mercado para el prompt del LLM (subfase 3.6, fase iv): ATR,
volatilidad, correlacion con BTC, funding y posiciones reales abiertas, todo
leido de la cache local -- y las 3 pruebas de fuga obligatorias de
docs/FASE3_6_LLM.md seccion (a)."""

from __future__ import annotations

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


def test_atr14_pct_is_a_small_positive_fraction_for_calm_prices():
    bars = make_bars("BTCUSDT", rising_closes(20, start=100.0, step=0.1))
    atr = mf.compute_atr14_pct(bars)
    assert atr is not None and 0.0 < atr < 0.01


def test_returns_std_is_none_with_fewer_than_required_bars():
    bars = make_bars("BTCUSDT", rising_closes(10))
    assert mf.compute_returns_std(bars, n=30) is None


def test_returns_std_is_zero_for_a_perfectly_steady_percentage_move():
    closes = [100.0 * (1.01 ** i) for i in range(32)]  # mismo retorno cada vela
    bars = make_bars("BTCUSDT", closes)
    std = mf.compute_returns_std(bars, n=30)
    assert std is not None and std == pytest.approx(0.0, abs=1e-9)


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

    assert features["funding_rate_pct"] == 0.0002
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

    assert features["funding_rate_pct"] == 0.0001
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
    assert features["funding_rate_pct"] == 0.0001  # nunca ve el evento futuro


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
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(40))
    atr_before = mf.compute_atr14_pct(await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 40))
    std_before = mf.compute_returns_std(
        await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 40), n=30
    )

    # Velas futuras con valores extremos, DESPUES de as_of_ms.
    extreme_future = [
        OHLCVBar(symbol="BTCUSDT", interval="1h", price_type="LAST_PRICE",
                 open_time=as_of_ms + i * HOUR_MS, open=1e9, high=1e9, low=1.0, close=1e9)
        for i in range(5)
    ]
    await ohlcv_repo.upsert_bars(db, extreme_future)

    bars_after = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 40)
    atr_after = mf.compute_atr14_pct(bars_after)
    std_after = mf.compute_returns_std(bars_after, n=30)

    assert atr_after == atr_before
    assert std_after == std_before


@pytest.mark.asyncio
async def test_leakage_full_series_vs_truncated_at_close_give_the_same_result(db):
    closes = rising_closes(40)
    as_of_ms = BASE_MS + 29 * HOUR_MS + HOUR_MS  # cierre de la vela #29 (0-indexada)

    # Base "truncada": solo las velas hasta as_of_ms existen.
    await ohlcv_repo.upsert_bars(db, make_bars("BTCUSDT", closes[:30]))
    truncated_bars = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, 30)
    truncated_atr = mf.compute_atr14_pct(truncated_bars)

    # Misma base, pero ahora con el resto de la serie "completa" (el futuro real).
    await ohlcv_repo.upsert_bars(db, make_bars("BTCUSDT", closes))
    full_bars_fetched_as_of_the_same_close = await mf.fetch_closed_candles(
        db, "BTCUSDT", as_of_ms, 30
    )
    full_atr_as_of_the_same_close = mf.compute_atr14_pct(full_bars_fetched_as_of_the_same_close)

    assert [b.open_time for b in truncated_bars] == [
        b.open_time for b in full_bars_fetched_as_of_the_same_close
    ]
    assert truncated_atr == full_atr_as_of_the_same_close


@pytest.mark.asyncio
async def test_leakage_no_feature_ever_uses_a_bar_with_open_time_after_the_close(db):
    as_of_ms = await seed(db, "BTCUSDT", rising_closes(40))
    await ohlcv_repo.upsert_bars(db, make_bars(
        "BTCUSDT", rising_closes(5), start_ms=as_of_ms + HOUR_MS,
    ))

    bars = await mf.fetch_closed_candles(db, "BTCUSDT", as_of_ms, limit=100)

    assert all(b.open_time <= as_of_ms for b in bars)
    assert all(b.open_time + HOUR_MS <= as_of_ms for b in bars)  # mas estricto: vela ya cerrada
