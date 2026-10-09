"""Tabla `llm_logs` (subfase 3.6): una fila por llamada al LLM sobre una senal
agrupada, exitosa o no. Ver docs/FASE3_6_LLM.md, seccion (c)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database


async def insert(
    db: Database,
    *,
    signal_group_key: str,
    shadow_trade_id: int | None,
    fase: str,
    candle_close_time: datetime,
    decision_delay_s: float,
    hour_utc: int,
    atr_pct: float | None,
    model: str,
    prompt_version: str,
    prompt_sha256: str,
    prompt: str,
    response_raw: str | None,
    status: str,
    error: str | None,
    decision: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float,
    latency_ms: int | None,
    created_at: datetime | None = None,
) -> int:
    """Una fila por `signal_group_key` (UNIQUE): una sola llamada por grupo, sin
    reintentos. Devuelve el id insertado."""
    cursor = await db.execute(
        """
        INSERT INTO llm_logs (
            created_at, signal_group_key, shadow_trade_id, fase, candle_close_time,
            decision_delay_s, hour_utc, atr_pct, model, prompt_version, prompt_sha256,
            prompt, response_raw, status, error, decision, input_tokens, output_tokens,
            cost_usd, latency_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (created_at or datetime.now(UTC)).isoformat(), signal_group_key, shadow_trade_id,
            fase, candle_close_time.isoformat(), decision_delay_s, hour_utc, atr_pct, model,
            prompt_version, prompt_sha256, prompt, response_raw, status, error, decision,
            input_tokens, output_tokens, cost_usd, latency_ms,
        ),
    )
    return cursor.lastrowid


async def get_spend_since(db: Database, since: datetime) -> float:
    """Suma `cost_usd` de las filas con `created_at >= since` -- usada para el
    gasto del dia (seccion e); el llamador calcula el inicio del dia local con
    `app.llm.budget.local_day_start_utc`."""
    row = await db.fetch_one(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_logs WHERE created_at >= ?",
        (since.isoformat(),),
    )
    return float(row["total"]) if row else 0.0


async def get_by_signal_group_key(db: Database, signal_group_key: str):
    return await db.fetch_one(
        "SELECT * FROM llm_logs WHERE signal_group_key = ?", (signal_group_key,)
    )
