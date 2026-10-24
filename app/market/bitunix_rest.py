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
    """Cliente REST publico de Bitunix Futures, con limite de tasa y reintentos con backoff.

    Reintenta los errores de red y los estados 429, 500, 502, 503 y 504 hasta `max_retries`
    veces (espera exponencial con jitter, tope 30 s); ante un timeout duro reconstruye el
    cliente HTTP. NUNCA envia `api-key` ni firma: v1 es paper trading.

    Attributes:
        base_url: URL base sin barra final.
        rate_limiter: `RateLimiter` compartido por todas las llamadas del cliente.
        max_retries: reintentos extra tras el primer intento.
        timeout: segundos maximos por peticion.
    """
    def __init__(
        self,
        base_url: str,
        rate_limit_per_sec: int = 4,
        max_retries: int = 5,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = RateLimiter(rate_limit_per_sec)
        self.max_retries = max_retries
        self.timeout = timeout
        self._client = build_async_http_client(timeout=timeout)

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente.

        Efectos:
            Cierra las conexiones de red.
        """
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET con limite de tasa, reintentos y validacion del `code` del payload.

        Descarta los parametros con valor `None`. Un 200 con `code` distinto de 0 es un error de
        la API aunque el HTTP sea correcto.

        Args:
            path: ruta bajo `base_url` (p. ej. `/api/v1/futures/market/kline`).
            params: parametros de consulta; los `None` se omiten.

        Returns:
            El campo `data` del payload (o el payload entero si no es un dict).

        Raises:
            BitunixApiError: si la API responde 200 con `code` distinto de 0.
            httpx.HTTPStatusError: si el estado no es reintentable, o se agotan los reintentos.
            TimeoutError: si se agotan los reintentos tras timeouts duros.
            httpx.TransportError: si se agotan los reintentos por errores de red.

        Efectos:
            Red: GET publico a Bitunix.
        """
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.rate_limiter.acquire()
            try:
                # Respaldo ademas del timeout propio de httpx (defensa en
                # profundidad frente a un cuelgue real de socket; no hay
                # evidencia de que esto haya ocurrido nunca en la practica
                # -- ver docs/FASE2_BLOQUEO_RED.md, el diagnostico real del
                # "cuelgue" fue computo lento sin logging, no la red).
                response = await asyncio.wait_for(
                    self._client.get(url, params=params), timeout=self.timeout
                )
            except TimeoutError as exc:
                last_exc = exc
                logger.warning("Timeout duro en %s (intento %d): %s", path, attempt, exc)
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
        """Especificaciones de contrato (`GET /api/v1/futures/market/trading_pairs`).

        Args:
            symbol: si se da, solo ese contrato; si no, todos.

        Returns:
            Lista de diccionarios con `symbol`, `minTradeVolume`, `basePrecision`,
            `quotePrecision`, `minLeverage`, `maxLeverage`, `symbolStatus`, etc.

        Raises:
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
        data = await self._get("/api/v1/futures/market/trading_pairs", {"symbol": symbol})
        return data if isinstance(data, list) else [data]

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        """Tickers con el ultimo precio (`GET /api/v1/futures/market/tickers`).

        Args:
            symbol: si se da, solo ese simbolo; si no, todos.

        Returns:
            Lista de diccionarios de ticker (incluye `symbol` y `lastPrice`).

        Raises:
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
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
        """Velas (`GET /api/v1/futures/market/kline`); el maximo verificado es 200 por llamada.

        Con `end_time` la API devuelve velas con `open_time` ESTRICTAMENTE menor y pagina hacia
        atras (ver `app/market/ohlcv_history.py`).

        Args:
            symbol: simbolo, p. ej. `BTCUSDT`.
            interval: intervalo (`1m`, `1h`, `4h`, `1d`, ...).
            start_time: inicio en ms Unix, o `None`.
            end_time: fin en ms Unix (exclusivo sobre `open_time`), o `None`.
            limit: velas a pedir (maximo 200).
            price_type: `LAST_PRICE` o `MARK_PRICE`.

        Returns:
            Lista de diccionarios con `time`, `open`, `high`, `low`, `close`, `baseVol`, `quoteVol`.

        Raises:
            ValueError: si `limit` supera 200.
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
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
        """Profundidad del libro (`GET /api/v1/futures/market/depth`).

        Args:
            symbol: simbolo.
            limit: niveles a pedir, o `None` para el valor por defecto de la API.

        Returns:
            El diccionario de profundidad que devuelve la API.

        Raises:
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
        return await self._get("/api/v1/futures/market/depth", {"symbol": symbol, "limit": limit})

    async def get_funding_rate_batch(self) -> list[dict]:
        """Funding vigente de todos los simbolos (`GET .../market/funding_rate/batch`).

        No se documento un parametro de filtro verificado: para un solo simbolo, filtrar del
        lado del cliente sobre el resultado.

        Returns:
            Lista de diccionarios con `symbol`, `fundingRate`, `fundingInterval` y
            `nextFundingTime`.

        Raises:
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
        data = await self._get("/api/v1/futures/market/funding_rate/batch")
        return data if isinstance(data, list) else [data]

    async def get_funding_rate_history(
        self,
        symbol: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Historial de funding (`GET .../market/get_funding_rate_history`); maximo 200 por llamada.

        La profundidad historica real no esta documentada. Si resulta insuficiente se aproxima el
        resto con el funding vigente aplicado hacia atras (`app/backtesting/funding.py`).

        Args:
            symbol: simbolo.
            start_time: inicio en ms Unix, o `None`.
            end_time: fin en ms Unix, o `None`.
            limit: registros a pedir (maximo 200).

        Returns:
            Lista de diccionarios con `fundingTime` y `fundingRate`.

        Raises:
            ValueError: si `limit` supera 200.
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
        if limit > 200:
            raise ValueError("Bitunix limita get_funding_rate_history a 200 registros por llamada")
        data = await self._get(
            "/api/v1/futures/market/get_funding_rate_history",
            {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": limit},
        )
        return data if isinstance(data, list) else []

    async def get_position_tiers(self, symbol: str) -> list[dict]:
        """Tramos de margen de mantenimiento por nocional (`GET .../position/get_position_tiers`).

        Se usan para simular la liquidacion. El parametro `symbol` se asume por consistencia con
        el resto de la API; no quedo verificado explicitamente en la documentacion consultada
        (se comprobo en el primer uso real, ver las pruebas de integracion).

        Args:
            symbol: simbolo.

        Returns:
            Lista de tramos con `level`, `startValue`, `endValue`, `leverage` y
            `maintenanceMarginRate`.

        Raises:
            BitunixApiError: si la API responde con un codigo de error.

        Efectos:
            Red: GET publico a Bitunix.
        """
        data = await self._get("/api/v1/futures/position/get_position_tiers", {"symbol": symbol})
        return data if isinstance(data, list) else [data]


class BitunixApiError(RuntimeError):
    """La API de Bitunix respondio 200 pero con un codigo de error en el payload."""
