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
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LlmTimeoutError(Exception):
    """La llamada no respondio dentro de `LLM_TIMEOUT_SECONDS`."""


class LlmHttpError(Exception):
    """La API respondio con un error de red o de estado HTTP (no un timeout)."""


class LlmClient(Protocol):
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
    """Fabrica del cliente real. Import diferido a proposito (ver docstring del
    modulo): construye `AnthropicLlmClient`, que a su vez crea el
    `anthropic.AsyncAnthropic` con la clave de `settings.anthropic_api_key`
    (leida solo de `.env` por pydantic-settings; el asistente nunca la lee)."""
    from app.llm._anthropic_client import AnthropicLlmClient  # diferido a proposito

    return AnthropicLlmClient(settings)
