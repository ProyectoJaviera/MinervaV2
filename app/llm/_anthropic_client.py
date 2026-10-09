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

**`max_retries=0` (ajuste 1 a la fase i, 2026-10-09).** El SDK reintenta por
defecto timeouts, 408/409/429 y >=500 con `max_retries=2`: con
`timeout=20s` eso puede estirar una sola llamada "logica" hasta
`20s * (reintentos+1)` = 60 s, justo el limite de retraso maximo para abrir
en la cuenta real (`LLM_REAL_MAX_DELAY_SECONDS`). Como el diseño ya dice
"una sola llamada por grupo, sin reintentos" (seccion b), `max_retries=0`
hace que esa regla tambien valga a nivel de transporte: un fallo deja
SIN_LLM rapido, dentro de los 20 s, en vez de reintentar en silencio.
"""

from __future__ import annotations

import time

import anthropic

from app.config import Settings
from app.llm.client import LlmHttpError, LlmRawResponse, LlmTimeoutError


class AnthropicLlmClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, max_retries=0
        )

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
