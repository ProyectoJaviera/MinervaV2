"""Cliente real del LLM con el SDK oficial de Anthropic (subfase 3.6).

Solo se importa este modulo desde `app.llm.client.build_anthropic_client`
(import diferido): ningun test sin red lo carga. Requiere `ANTHROPIC_API_KEY`
en `.env` (el asistente nunca la lee).

**Parametros de la llamada, verificados el 2026-10-09** contra la
documentacion vigente de la API de Anthropic (docs/FASE3_6_LLM.md, seccion b):
Sonnet 5.5 devuelve `400` si se envia `temperature`/`top_p`/`top_k` distinto
del de la API -- se omite. `thinking.type = "disabled"` tambien devuelve
`400` en este modelo; el ajuste de menor razonamiento es
`thinking = {"type": "between_tools"}` con `output_config = {"effort": "low"}`
(valido solo a efecto "high" o menor). Sin herramientas declaradas en la
llamada no genera bloques de razonamiento extendido, asi que no consume
`max_tokens` -- los 300 tokens quedan enteros para la respuesta JSON.
"""

from __future__ import annotations

import time

import anthropic

from app.config import Settings
from app.llm.client import LlmHttpError, LlmRawResponse, LlmTimeoutError


class AnthropicLlmClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def complete(
        self, *, system: str, user: str, model: str, max_tokens: int
    ) -> LlmRawResponse:
        start = time.monotonic()
        try:
            response = await self._client.with_options(
                timeout=self._settings.llm_timeout_seconds
            ).messages.create(
                model=model,
                max_tokens=max_tokens,
                thinking={"type": "between_tools"},
                output_config={"effort": "low"},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APITimeoutError as exc:
            raise LlmTimeoutError(str(exc)) from exc
        except (anthropic.APIConnectionError, anthropic.APIStatusError, anthropic.APIError) as exc:
            raise LlmHttpError(str(exc)) from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        text = "".join(block.text for block in response.content if block.type == "text")
        return LlmRawResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )
