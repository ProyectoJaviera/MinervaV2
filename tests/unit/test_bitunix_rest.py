from __future__ import annotations

import httpx
import pytest

from app.market.bitunix_rest import BitunixApiError, BitunixRestClient


def make_client_with_transport(transport: httpx.MockTransport) -> BitunixRestClient:
    client = BitunixRestClient(
        base_url="https://fapi.bitunix.com", rate_limit_per_sec=100, max_retries=3
    )
    client._client = httpx.AsyncClient(transport=transport)  # inyeccion para test, sin red real
    return client


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"code": -1, "msg": "rate limited"})
        return httpx.Response(
            200, json={"code": 0, "msg": "Success", "data": [{"symbol": "BTCUSDT"}]}
        )

    client = make_client_with_transport(httpx.MockTransport(handler))
    result = await client.get_trading_pairs("BTCUSDT")

    assert calls["n"] == 3
    assert result == [{"symbol": "BTCUSDT"}]
    await client.aclose()


@pytest.mark.asyncio
async def test_non_retryable_status_raises_immediately():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"msg": "not found"})

    client = make_client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_tickers("BTCUSDT")
    assert calls["n"] == 1  # no reintenta errores no-retryables
    await client.aclose()


@pytest.mark.asyncio
async def test_api_error_code_raises_bitunix_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10001, "msg": "invalid symbol", "data": None})

    client = make_client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(BitunixApiError):
        await client.get_kline("BADSYMBOL", "4h")
    await client.aclose()


@pytest.mark.asyncio
async def test_kline_rejects_limit_above_200():
    client = make_client_with_transport(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError):
        await client.get_kline("BTCUSDT", "4h", limit=201)
    await client.aclose()


@pytest.mark.asyncio
async def test_get_kline_parses_response_fields():
    raw_bar = {
        "open": "60000", "high": "60001", "close": "60000", "low": "59989.2",
        "time": 111111, "quoteVol": "1", "baseVol": "60000", "type": "LAST_PRICE",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "msg": "Success", "data": [raw_bar]})

    client = make_client_with_transport(httpx.MockTransport(handler))
    bars = await client.get_kline("BTCUSDT", "4h")
    assert bars == [raw_bar]
    await client.aclose()
