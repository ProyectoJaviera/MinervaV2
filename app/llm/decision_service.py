"""Servicio de decision del LLM sobre una senal agrupada (subfase 3.6, fase i).

Junta el cliente del LLM, `llm_logs`, el presupuesto con reserva y el
semaforo de concurrencia. Escribe la etiqueta en `shadow_trades` una sola vez
(seccion d). **No esta conectado al ciclo del bot** (`app/core/scheduler.py`):
eso es una decision aparte -- `AUTO_OPEN_WITHOUT_LLM` sigue en `false` durante
toda la 3.6 (ver docs/FASE3_6_LLM.md, "Decisiones", punto 8).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from app.config import Settings
from app.llm.budget import LlmBudgetTracker
from app.llm.client import LlmClient, LlmHttpError, LlmTimeoutError, build_anthropic_client
from app.llm.schemas import DECISION_TO_LABEL, LlmDecisionOut
from app.persistence.database import Database
from app.persistence.repositories import llm_logs_repo, shadow_repo

STATUS_OK = "OK"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_ERROR_HTTP = "ERROR_HTTP"
STATUS_INVALID = "INVALID"
STATUS_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

SIN_LLM = "SIN_LLM"

PILOTO = "PILOTO"
MEDICION = "MEDICION"


def estimate_cost_usd(input_tokens: int, output_tokens: int, settings: Settings) -> float:
    """Coste en USD con los precios configurados (seccion e)."""
    return (
        input_tokens * settings.llm_price_input_per_mtok / 1_000_000
        + output_tokens * settings.llm_price_output_per_mtok / 1_000_000
    )


def real_open_allowed(label: str, decision_delay_s: float, settings: Settings) -> bool:
    """Regla de apertura de la cuenta real (seccion g): solo con APROBADA y si la
    decision llego a tiempo. No la usa nada del ciclo del bot todavia (ver
    docstring del modulo); queda lista para cuando se conecte."""
    return label == "APROBADA" and decision_delay_s <= settings.llm_real_max_delay_seconds


@dataclass(frozen=True)
class LlmDecisionResult:
    status: str
    label: str  # "APROBADA" | "RECHAZADA" | "SIN_LLM"
    decision_delay_s: float
    log_id: int
    error: str | None = None


class LlmDecisionService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        client_factory=build_anthropic_client,
        fase: str = MEDICION,
    ) -> None:
        self._db = db
        self._settings = settings
        self._client_factory = client_factory
        self._client: LlmClient | None = None
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self._budget = LlmBudgetTracker()
        self.fase = fase

    def _get_client(self) -> LlmClient:
        if self._client is None:
            self._client = self._client_factory(self._settings)
        return self._client

    async def decide_group(
        self,
        *,
        signal_group_key: str,
        shadow_trade_id: int | None,
        candle_close_time: datetime,
        system_prompt: str,
        prompt_version: str,
        user_message: str,
        estimated_input_tokens: int,
        atr_pct: float | None = None,
        now: datetime | None = None,
    ) -> LlmDecisionResult:
        now = now or datetime.now(UTC)
        decision_delay_s = (now - candle_close_time).total_seconds()
        hour_utc = candle_close_time.astimezone(UTC).hour
        prompt_sha256 = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        model = self._settings.anthropic_sonnet_model
        full_prompt = f"{system_prompt}\n\n{user_message}"
        cost_max = estimate_cost_usd(
            estimated_input_tokens, self._settings.llm_max_tokens, self._settings
        )

        async def _log(**overrides) -> int:
            fields = {
                "signal_group_key": signal_group_key, "shadow_trade_id": shadow_trade_id,
                "fase": self.fase, "candle_close_time": candle_close_time,
                "decision_delay_s": decision_delay_s, "hour_utc": hour_utc, "atr_pct": atr_pct,
                "model": model, "prompt_version": prompt_version, "prompt_sha256": prompt_sha256,
                "prompt": full_prompt, "response_raw": None, "error": None, "decision": None,
                "input_tokens": None, "output_tokens": None, "cost_usd": 0.0, "latency_ms": None,
                "created_at": now,
            }
            fields.update(overrides)
            return await llm_logs_repo.insert(self._db, **fields)

        async with self._semaphore:
            reserved = await self._budget.try_reserve(self._db, self._settings, cost_max, now)
            if not reserved:
                log_id = await _log(
                    status=STATUS_BUDGET_EXCEEDED, error="presupuesto diario agotado"
                )
                return LlmDecisionResult(
                    STATUS_BUDGET_EXCEEDED, SIN_LLM, decision_delay_s, log_id,
                    "presupuesto diario agotado",
                )

            try:
                return await self._call_and_label(
                    model=model, max_tokens=self._settings.llm_max_tokens,
                    system_prompt=system_prompt, user_message=user_message,
                    shadow_trade_id=shadow_trade_id, decision_delay_s=decision_delay_s,
                    log=_log,
                )
            finally:
                await self._budget.release(cost_max)

    async def _call_and_label(
        self, *, model, max_tokens, system_prompt, user_message, shadow_trade_id,
        decision_delay_s, log,
    ) -> LlmDecisionResult:
        try:
            raw = await self._get_client().complete(
                system=system_prompt, user=user_message, model=model, max_tokens=max_tokens,
            )
        except LlmTimeoutError as exc:
            log_id = await log(status=STATUS_TIMEOUT, error=str(exc)[:500])
            return LlmDecisionResult(STATUS_TIMEOUT, SIN_LLM, decision_delay_s, log_id, str(exc))
        except LlmHttpError as exc:
            log_id = await log(status=STATUS_ERROR_HTTP, error=str(exc)[:500])
            return LlmDecisionResult(STATUS_ERROR_HTTP, SIN_LLM, decision_delay_s, log_id, str(exc))

        real_cost = estimate_cost_usd(raw.input_tokens, raw.output_tokens, self._settings)
        try:
            parsed = LlmDecisionOut.model_validate_json(raw.text)
        except ValidationError as exc:
            log_id = await log(
                status=STATUS_INVALID, error=str(exc)[:500], response_raw=raw.text,
                input_tokens=raw.input_tokens, output_tokens=raw.output_tokens,
                cost_usd=real_cost, latency_ms=raw.latency_ms,
            )
            return LlmDecisionResult(
                STATUS_INVALID, SIN_LLM, decision_delay_s, log_id, "respuesta invalida"
            )

        label = DECISION_TO_LABEL[parsed.decision]
        log_id = await log(
            status=STATUS_OK, response_raw=raw.text, decision=parsed.decision,
            input_tokens=raw.input_tokens, output_tokens=raw.output_tokens,
            cost_usd=real_cost, latency_ms=raw.latency_ms,
        )
        if shadow_trade_id is not None:
            await shadow_repo.set_llm_decision(self._db, shadow_trade_id, label)
        return LlmDecisionResult(STATUS_OK, label, decision_delay_s, log_id)
