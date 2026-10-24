"""Cliente HTTP asincrono fino de la Bot API de Telegram (solo `getUpdates` y
`sendMessage`). Parametros verificados en la documentacion oficial
(core.telegram.org/bots/api, 2026-10-17): `getUpdates` acepta `offset`, `limit`
(1-100), `timeout` (long polling, en segundos) y `allowed_updates`; `sendMessage`,
`chat_id` y `text`. Un update queda confirmado al llamar `getUpdates` con un
`offset` mayor que su `update_id`.

**El token nunca sale de aqui**: la URL lo lleva, asi que ninguna excepcion de
`httpx` (cuyo mensaje puede incluir la URL) se propaga tal cual -- se convierte en
`TelegramApiError` con el TIPO de la excepcion y se corta el encadenamiento
(`from None`) para que ni un `logger.exception` lo imprima."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096
# Margen sobre el `timeout` de long polling del servidor para que el cliente no
# corte una espera legitima.
HTTP_TIMEOUT_MARGIN_SECONDS = 15.0
SEND_TIMEOUT_SECONDS = 15.0


class TelegramApiError(Exception):
    """Fallo de la Bot API o de la red. Nunca incluye el token ni la URL.

    Attributes:
        status_code: estado HTTP, si hubo respuesta.
        retry_after: segundos que pide esperar la API ante un 429 (flood control).
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_after(body: Any) -> float | None:
    """`parameters.retry_after` de una respuesta de error, o `None` si no viene o no es valido."""
    if not isinstance(body, dict):
        return None
    parameters = body.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


class TelegramClient:
    """Cliente HTTP asincrono fino de la Bot API de Telegram: solo `getUpdates` y `sendMessage`.

    El token nunca sale de aqui: la URL lo lleva, asi que ninguna excepcion de `httpx` se
    propaga tal cual; se convierte en `TelegramApiError` con el TIPO de la excepcion y se
    corta el encadenamiento para que ni un `logger.exception` lo imprima.
    """
    def __init__(
        self, token: str, *, base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"{base_url}/bot{token}", transport=transport,
        )

    async def aclose(self) -> None:
        """Cierra el cliente HTTP.

        Efectos:
            Cierra las conexiones de red.
        """
        await self._http.aclose()

    async def _call(self, method: str, payload: dict[str, Any], timeout: float) -> Any:
        try:
            response = await self._http.post(f"/{method}", json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TelegramApiError(f"{method}: {type(exc).__name__}") from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramApiError(
                f"{method}: respuesta no JSON (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from None
        if response.status_code != 200 or not isinstance(body, dict) or not body.get("ok"):
            code = body.get("error_code") if isinstance(body, dict) else None
            suffix = f" (error_code {code})" if code else ""
            raise TelegramApiError(
                f"{method}: HTTP {response.status_code}{suffix}",
                status_code=response.status_code,
                retry_after=_retry_after(body),
            )
        return body.get("result")

    async def get_updates(
        self, *, offset: int | None, timeout: int, limit: int = 100,
    ) -> list[dict]:
        """Pide los updates nuevos (`getUpdates`, solo mensajes).

        Un `offset` mayor que un `update_id` confirma los anteriores.

        Args:
            offset: primer `update_id` que se quiere, o `None`.
            timeout: segundos de long-polling (0 = sin espera).
            limit: maximo de updates (1-100).

        Returns:
            La lista de updates.

        Raises:
            TelegramApiError: si la API o la red fallan, o el resultado no es una lista.

        Efectos:
            Red: POST a la Bot API de Telegram.
        """
        payload: dict[str, Any] = {
            "timeout": timeout, "limit": limit, "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call(
            "getUpdates", payload, timeout=timeout + HTTP_TIMEOUT_MARGIN_SECONDS
        )
        if not isinstance(result, list):
            raise TelegramApiError("getUpdates: resultado inesperado")
        return result

    async def send_message(self, chat_id: int | str, text: str) -> None:
        """Envia un mensaje de texto plano (se recorta a 4096 caracteres).

        Args:
            chat_id: chat de destino.
            text: texto.

        Raises:
            TelegramApiError: si la API o la red fallan.

        Efectos:
            Red: POST a la Bot API de Telegram.
        """
        await self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text[:MAX_MESSAGE_CHARS]},
            timeout=SEND_TIMEOUT_SECONDS,
        )
