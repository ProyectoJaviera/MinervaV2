"""Cliente del LLM como interfaz (subfase 3.6, fase i).

`LlmClient` es el unico punto de contacto con la red. El cliente real
(`AnthropicLlmClient`, en `app/llm/_anthropic_client.py`) se importa de forma
diferida desde `build_anthropic_client`, para que ningun test que no llame a
esa funcion necesite el SDK instalado ni toque la red -- ver
docs/FASE3_6_LLM.md, seccion (g): "Un test comprueba que no se construye
ningun cliente real: la fabrica del cliente se parchea para lanzar un error
si se invoca."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LlmRawResponse:
    """Respuesta cruda de una llamada al LLM: texto, tokens de entrada y salida y latencia."""
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LlmTimeoutError(Exception):
    """La llamada no respondio dentro de `LLM_TIMEOUT_SECONDS`."""


class LlmHttpError(Exception):
    """La API respondio con un error de red o de estado HTTP (no un timeout)."""


class LlmClient(Protocol):
    """Interfaz del cliente del LLM: el unico punto de contacto con la red.

    Las implementaciones son `AnthropicLlmClient` (real) y `FakeLlmClient` (pruebas). Su
    unico metodo, `complete(*, system, user, model, max_tokens)`, hace UNA llamada al
    modelo y devuelve una `LlmRawResponse`; lanza `LlmTimeoutError` si no responde a
    tiempo y `LlmHttpError` ante un error de red o de estado HTTP.
    """
    async def complete(
        self, *, system: str, user: str, model: str, max_tokens: int
    ) -> LlmRawResponse: ...


class FakeLlmClient:
    """Cliente programable para tests, sin red (docs/FASE3_6_LLM.md, seccion g).

    Cada llamada a `complete` consume la siguiente entrada de `responses`: si es
    una `LlmRawResponse` la devuelve, si es una excepcion la relanza."""

    def __init__(self, responses: list[LlmRawResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(
        self, *, system: str, user: str, model: str, max_tokens: int
    ) -> LlmRawResponse:
        """Devuelve la siguiente respuesta programada, o relanza la excepcion programada.

        Args:
            system: texto del sistema (se registra en `calls`).
            user: mensaje de usuario (se registra en `calls`).
            model: id del modelo (se registra en `calls`).
            max_tokens: tope de tokens (se registra en `calls`).

        Returns:
            La `LlmRawResponse` programada.

        Raises:
            AssertionError: si no quedan respuestas programadas.
            Exception: la excepcion programada, si la siguiente entrada es una.
        """
        self.calls.append(
            {"system": system, "user": user, "model": model, "max_tokens": max_tokens}
        )
        if not self._responses:
            raise AssertionError("FakeLlmClient: no quedan respuestas programadas")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def build_anthropic_client(settings) -> LlmClient:
    """Fabrica del cliente real, con import diferido a proposito.

    Asi ninguna prueba que no la llame necesita el SDK ni toca la red. Construye
    `AnthropicLlmClient`, que crea el cliente asincrono de Anthropic con la clave de
    `settings.anthropic_api_key` (leida solo de `.env` por pydantic-settings).

    Args:
        settings: configuracion (clave de Anthropic y timeout).

    Returns:
        Un `LlmClient` real.
    """
    from app.llm._anthropic_client import AnthropicLlmClient  # diferido a proposito

    return AnthropicLlmClient(settings)
