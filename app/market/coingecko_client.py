"""Cliente de CoinGecko (plan Demo gratuito, verificado en docs/FASE0.md y
docs/FASE2_PLAN.md). Construido con `app/core/http.py::build_async_http_client`
-- regla establecida en Fase 1: todo cliente HTTP nuevo debe usar ese modulo
centralizado (truststore para redes con inspeccion TLS corporativa).

Endpoints usados (verificados contra la documentacion oficial y con
llamadas reales durante la investigacion de Fase 2):
- GET /coins/markets?vs_currency=usd&order=market_cap_desc&per_page=N
  (ranking general por capitalizacion).
- GET /coins/markets?vs_currency=usd&category=<id>&per_page=250
  (listado de monedas de una categoria -- usado para excluir stablecoins/
  wrapped/staking; el parametro `category` SOLO filtra por inclusion, no
  existe forma de excluir una categoria en la misma llamada).
- GET /coins/categories/list (ids de categoria disponibles; respondio 200
  incluso sin API key durante las pruebas, pero el plan Demo formal exige
  una key gratuita -- no depender de ese comportamiento).

El header `x-cg-demo-api-key` se envia solo si `COINGECKO_API_KEY` esta
configurada (puede quedar vacio, como toda credencial, en `.env.example`).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.http import build_async_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CoinGeckoApiError(RuntimeError):
    """La API de CoinGecko respondio con un error tras agotar los reintentos."""


class CoinGeckoClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        max_retries: int = 4,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self._client = build_async_http_client(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"x-cg-demo-api-key": self.api_key} if self.api_key else {}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning("Error de red en CoinGecko %s (intento %d): %s", path, attempt, exc)
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                last_exc = httpx.HTTPStatusError(
                    f"status {response.status_code}", request=response.request, response=response
                )
                logger.warning(
                    "CoinGecko respondio %s en %s (intento %d)", response.status_code, path, attempt
                )
            if attempt < self.max_retries:
                backoff = min(2**attempt, 20) + random.uniform(0, 0.5)
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise CoinGeckoApiError(f"{path} fallo tras {self.max_retries + 1} intentos") from last_exc

    async def get_markets(
        self, per_page: int = 30, category: str | None = None, vs_currency: str = "usd"
    ) -> list[dict]:
        """GET /coins/markets. Ordenado por market_cap_desc. `category`
        filtra por inclusion (no hay parametro de exclusion)."""
        data = await self._get(
            "/coins/markets",
            {
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": per_page,
                "category": category,
            },
        )
        return data if isinstance(data, list) else []

    async def get_categories_list(self) -> list[dict]:
        """GET /coins/categories/list -- ids de categoria disponibles."""
        data = await self._get("/coins/categories/list")
        return data if isinstance(data, list) else []
