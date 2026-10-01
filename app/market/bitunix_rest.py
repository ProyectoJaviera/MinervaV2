"""Cliente REST publico de Bitunix Futures.

Solo endpoints publicos de mercado (sin api-key/firma), verificados contra
la documentacion oficial en `www.bitunix.com/api-docs/futures/...` (ver
docs/FASE0.md seccion 2). Base URL: https://fapi.bitunix.com. Limite
conocido: 10 req/s por IP en todos los endpoints usados aqui.

Regla critica de CLAUDE.md: este cliente NUNCA envia `api-key`, `sign` ni
ningun otro header de autenticacion -- v1 es 100% paper trading y no usa
credenciales de Bitunix.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.http import build_async_http_client
from app.core.logging import get_logger
from app.market.rate_limiter import RateLimiter

logger = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BitunixRestClient:
    def __init__(
        self,
        base_url: str,
        rate_limit_per_sec: int = 10,
        max_retries: int = 5,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = RateLimiter(rate_limit_per_sec)
        self.max_retries = max_retries
        self.timeout = timeout
        self._client = build_async_http_client(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.rate_limiter.acquire()
            try:
                # Respaldo duro ademas del timeout de httpx: se observo en
                # la corrida real de Fase 2 una conexion colgada que el
                # timeout normal de httpx no corto (posible interaccion
                # asyncio/httpx en Windows) -- wait_for garantiza que esta
                # llamada nunca bloquea mas de `timeout * 2`.
                response = await asyncio.wait_for(
                    self._client.get(url, params=params), timeout=self.timeout
                )
            except TimeoutError as exc:
                last_exc = exc
                logger.warning("Timeout duro en %s (intento %d): %s", path, attempt, exc)
                # Se observo que el timeout duro por si solo no bastaba: el
                # siguiente intento volvia a colgarse igual, consistente con
                # una conexion persistente (keep-alive) del pool quedando en
                # mal estado. Se descarta el cliente entero y se construye
                # uno nuevo antes de reintentar.
                try:
                    await asyncio.wait_for(self._client.aclose(), timeout=5.0)
                except Exception:  # noqa: BLE001 - el cierre tambien puede colgarse/fallar
                    pass
                self._client = build_async_http_client(timeout=self.timeout)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning("Error de red en %s (intento %d): %s", path, attempt, exc)
            else:
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("code") not in (0, "0", None):
                        raise BitunixApiError(
                            f"{path} respondio code={payload.get('code')} msg={payload.get('msg')}"
                        )
                    return payload.get("data") if isinstance(payload, dict) else payload
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                last_exc = httpx.HTTPStatusError(
                    f"status {response.status_code}", request=response.request, response=response
                )
                logger.warning(
                    "Respuesta %s en %s (intento %d)", response.status_code, path, attempt
                )
            if attempt < self.max_retries:
                backoff = min(2**attempt, 30) + random.uniform(0, 0.5)
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    async def get_trading_pairs(self, symbol: str | None = None) -> list[dict]:
        """GET /api/v1/futures/market/trading_pairs -- specs de contrato."""
        data = await self._get("/api/v1/futures/market/trading_pairs", {"symbol": symbol})
        return data if isinstance(data, list) else [data]

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        """GET /api/v1/futures/market/tickers."""
        data = await self._get("/api/v1/futures/market/tickers", {"symbol": symbol})
        return data if isinstance(data, list) else [data]

    async def get_kline(
        self,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
        price_type: str = "LAST_PRICE",
    ) -> list[dict]:
        """GET /api/v1/futures/market/kline. `limit` maximo verificado: 200."""
        if limit > 200:
            raise ValueError("Bitunix limita /market/kline a 200 velas por llamada")
        data = await self._get(
            "/api/v1/futures/market/kline",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
                "limit": limit,
                "type": price_type,
            },
        )
        return data if isinstance(data, list) else []

    async def get_depth(self, symbol: str, limit: int | None = None) -> dict:
        """GET /api/v1/futures/market/depth."""
        return await self._get("/api/v1/futures/market/depth", {"symbol": symbol, "limit": limit})

    async def get_funding_rate_batch(self) -> list[dict]:
        """GET /api/v1/futures/market/funding_rate/batch -- funding vigente, todos los
        simbolos. No se documento un parametro de filtro verificado; si se necesita un
        solo simbolo, filtrar client-side sobre el resultado."""
        data = await self._get("/api/v1/futures/market/funding_rate/batch")
        return data if isinstance(data, list) else [data]

    async def get_funding_rate_history(
        self,
        symbol: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """GET /api/v1/futures/market/get_funding_rate_history. `limit` maximo: 200.

        La profundidad historica real no esta documentada explicitamente; si en la
        practica resulta insuficiente, se debe aproximar el resto con el funding rate
        vigente (`get_funding_rate_batch`) aplicado retroactivamente (ver docs/FASE0.md).
        """
        if limit > 200:
            raise ValueError("Bitunix limita get_funding_rate_history a 200 registros por llamada")
        data = await self._get(
            "/api/v1/futures/market/get_funding_rate_history",
            {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": limit},
        )
        return data if isinstance(data, list) else []

    async def get_position_tiers(self, symbol: str) -> list[dict]:
        """GET /api/v1/futures/position/get_position_tiers -- tiers de margen de
        mantenimiento por notional, usados en Fase 3 para simular liquidacion.

        El parametro `symbol` se asume por consistencia con el resto de la API de
        Bitunix (todos los demas endpoints de mercado/posicion lo usan); no quedo
        verificado explicitamente en la documentacion fetcheada. Se valida en el
        primer uso real (test de integracion) y se ajusta si la API lo rechaza.
        """
        data = await self._get("/api/v1/futures/position/get_position_tiers", {"symbol": symbol})
        return data if isinstance(data, list) else [data]


class BitunixApiError(RuntimeError):
    """La API de Bitunix respondio 200 pero con un codigo de error en el payload."""
