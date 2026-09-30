"""Test de integracion manual contra la API PUBLICA real de Bitunix.

No corre en CI por defecto (requiere red saliente real). Ejecutar a mano con:
    MINERVA_RUN_LIVE_TESTS=1 pytest tests/integration -m slow

No usa ninguna credencial (solo endpoints publicos de mercado).
"""

from __future__ import annotations

import os

import pytest

from app.market.bitunix_rest import BitunixRestClient

pytestmark = pytest.mark.skipif(
    os.environ.get("MINERVA_RUN_LIVE_TESTS") != "1",
    reason="Test de integracion contra la red real; se omite por defecto.",
)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_get_tickers_live_btcusdt():
    client = BitunixRestClient(base_url="https://fapi.bitunix.com")
    try:
        tickers = await client.get_tickers("BTCUSDT")
        assert tickers
        assert tickers[0]["symbol"] == "BTCUSDT"
        assert float(tickers[0]["lastPrice"]) > 0
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_get_kline_live_btcusdt():
    client = BitunixRestClient(base_url="https://fapi.bitunix.com")
    try:
        bars = await client.get_kline("BTCUSDT", "4h", limit=5)
        assert len(bars) <= 5
        assert all("open" in b and "time" in b for b in bars)
    finally:
        await client.aclose()
