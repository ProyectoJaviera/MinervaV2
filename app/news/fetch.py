"""Cliente HTTP de las fuentes de noticias: GET condicional (ETag / Last-Modified), User-Agent
propio, tope de tamano de respuesta, timeout duro, reintentos con backoff exponencial y jitter
para fallos de red y 5xx, y respeto de `Retry-After` en un 429 (que NO se reintenta dentro de la
llamada: lo gestiona el colector con un enfriamiento). Construido con
`app/core/http.py::build_async_http_client` (truststore), como todo cliente del proyecto. No sigue
redirecciones. Sin credenciales: ninguna de las fuentes integradas las necesita."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.core.http import build_async_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class FetchError(RuntimeError):
    """Fallo de red / 5xx tras agotar los reintentos."""


class ResponseTooLargeError(FetchError):
    """La respuesta supera `max_bytes`: se descarta sin guardarla."""


@dataclass(frozen=True)
class FetchResult:
    status: int
    content: bytes = b""
    etag: str | None = None
    last_modified: str | None = None
    retry_after_s: float | None = None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8-sig", errors="replace")

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def rate_limited(self) -> bool:
        return self.status == 429

    @property
    def ok(self) -> bool:
        return self.status == 200


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """`Retry-After` en segundos o como fecha HTTP. `None` si falta o no se entiende."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class HttpFetcher:
    def __init__(
        self, *, user_agent: str, max_bytes: int = 2_000_000, timeout: float = 15.0,
        max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport = transport
        self._sleep = sleep
        self._jitter = jitter
        self._client = self._build_client()

    def _build_client(self) -> httpx.AsyncClient:
        if self._transport is not None:  # pruebas: sin red
            return httpx.AsyncClient(transport=self._transport, timeout=self.timeout)
        return build_async_http_client(timeout=self.timeout)

    async def aclose(self) -> None:
        try:
            await asyncio.wait_for(self._client.aclose(), timeout=5.0)
        except Exception:  # noqa: BLE001, S110 - cierre best-effort
            pass

    async def get(
        self, url: str, *, params: dict | None = None, etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._once(url, params, headers), timeout=self.timeout + 5.0
                )
            except ResponseTooLargeError:
                raise
            except (TimeoutError, httpx.TransportError) as exc:
                last_error = exc
                logger.warning(
                    "Red/timeout en %s (intento %d): %s", url, attempt, type(exc).__name__
                )
                if isinstance(exc, TimeoutError):  # el pool puede quedar mal tras un timeout duro
                    await self.aclose()
                    self._client = self._build_client()
            else:
                if result.status not in RETRYABLE_STATUS:
                    return result
                last_error = FetchError(f"HTTP {result.status}")
                logger.warning("HTTP %d en %s (intento %d)", result.status, url, attempt)
            if attempt < self.max_retries:
                await self._sleep(min(2.0**attempt, 20.0) + self._jitter() * 0.5)
        raise FetchError(f"{url} fallo tras {self.max_retries + 1} intentos") from last_error

    async def _once(self, url: str, params: dict | None, headers: dict) -> FetchResult:
        async with self._client.stream(
            "GET", url, params=params, headers=headers, follow_redirects=False
        ) as response:
            status = response.status_code
            meta = dict(
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                retry_after_s=parse_retry_after(response.headers.get("retry-after")),
            )
            if status != 200:
                # cuerpo de error (p.ej. el texto de GDELT en un 429): corto y acotado
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > 4096:
                        break
                return FetchResult(status=status, content=body[:4096], **meta)
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                raise ResponseTooLargeError(f"{url}: {declared} bytes > {self.max_bytes}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self.max_bytes:
                    raise ResponseTooLargeError(f"{url}: respuesta mayor a {self.max_bytes} bytes")
            return FetchResult(status=200, content=bytes(body), **meta)
