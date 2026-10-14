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
    features_version: str | None = None,
    json_repaired: bool = False,
) -> int:
    """Una fila por `signal_group_key` (UNIQUE): una sola llamada por grupo, sin
    reintentos. Devuelve el id insertado.

    `features_version`/`json_repaired`: revision del piloto v2
    (docs/FASE3_6_LLM.md seccion k) -- ver el docstring de
    `_LLM_LOGS_MIGRATED_COLUMNS` en app/persistence/database.py."""
    cursor = await db.execute(
        """
        INSERT INTO llm_logs (
            created_at, signal_group_key, shadow_trade_id, fase, candle_close_time,
            decision_delay_s, hour_utc, atr_pct, model, prompt_version, prompt_sha256,
            prompt, response_raw, status, error, decision, input_tokens, output_tokens,
            cost_usd, latency_ms, features_version, json_repaired
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (created_at or datetime.now(UTC)).isoformat(), signal_group_key, shadow_trade_id,
            fase, candle_close_time.isoformat(), decision_delay_s, hour_utc, atr_pct, model,
            prompt_version, prompt_sha256, prompt, response_raw, status, error, decision,
            input_tokens, output_tokens, cost_usd, latency_ms, features_version,
            int(json_repaired),
        ),
    )
    return cursor.lastrowid


async def get_spend_since(
    db: Database, since: datetime, fase: str | None = None
) -> float:
    """Suma `cost_usd` de las filas con `created_at >= since` -- usada para el
    gasto del dia (seccion e); el llamador calcula el inicio del dia local con
    `app.llm.budget.local_day_start_utc`. `LlmBudgetTracker.try_reserve` la
    llama SIN `fase` a proposito (el presupuesto diario es global, no por
    fase); `fase` es para TAREA B (bloque `llm_labeling` de `GET /health`)."""
    query = "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_logs WHERE created_at >= ?"
    params: list = [since.isoformat()]
    if fase is not None:
        query += " AND fase = ?"
        params.append(fase)
    row = await db.fetch_one(query, tuple(params))
    return float(row["total"]) if row else 0.0


async def get_status_counts_since(
    db: Database, since: datetime, fase: str | None = None
) -> dict[str, int]:
    """Conteo de filas por `status` desde `since` (TAREA B, docs/
    FASE3_6_LLM.md seccion k): distingue `BUDGET_EXCEEDED` de `TIMEOUT`/
    `ERROR_HTTP`/`INVALID` -- todas terminan en SIN_LLM, pero por razones
    distintas. Usado por `GET /health` (bloque `llm_labeling`) y preparado
    para la seccion (m) del informe de sesgo de exclusion."""
    query = "SELECT status, COUNT(*) AS n FROM llm_logs WHERE created_at >= ?"
    params: list = [since.isoformat()]
    if fase is not None:
        query += " AND fase = ?"
        params.append(fase)
    query += " GROUP BY status"
    rows = await db.fetch_all(query, tuple(params))
    return {row["status"]: row["n"] for row in rows}


async def get_first_occurrence_since(
    db: Database, since: datetime, status: str, fase: str | None = None
) -> datetime | None:
    """`created_at` de la PRIMERA fila con ese `status` desde `since` --
    usado para registrar el momento en que se agoto el presupuesto diario
    (TAREA B, `status='BUDGET_EXCEEDED'`)."""
    query = "SELECT MIN(created_at) AS t FROM llm_logs WHERE created_at >= ? AND status = ?"
    params: list = [since.isoformat(), status]
    if fase is not None:
        query += " AND fase = ?"
        params.append(fase)
    row = await db.fetch_one(query, tuple(params))
    if row is None or row["t"] is None:
        return None
    return datetime.fromisoformat(row["t"])


async def get_by_signal_group_key(db: Database, signal_group_key: str):
    return await db.fetch_one(
        "SELECT * FROM llm_logs WHERE signal_group_key = ?", (signal_group_key,)
    )


async def get_recent(db: Database, limit: int = 50, fase: str | None = None):
    """Las `limit` filas mas recientes (`ORDER BY created_at DESC`), opcional-
    mente filtradas por `fase` -- Subfase C del plan pre-Fase 5 aprobado
    2026-10-14 (`GET /api/llm/decisions/recent`). Mismo patron que
    `trades_repo.get_trades(db, limit=100)`."""
    query = "SELECT * FROM llm_logs"
    params: list = []
    if fase is not None:
        query += " WHERE fase = ?"
        params.append(fase)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return await db.fetch_all(query, tuple(params))


async def get_piloto_signal_group_keys(db: Database) -> set[str]:
    """Grupos del piloto (prompt congelado, `fase = 'PILOTO'`), excluidos de la
    medicion (subfase 3.6, seccion k). `app.trading.ai_value.ai_value_verdict`
    los filtra con esto antes de calcular el veredicto."""
    rows = await db.fetch_all("SELECT signal_group_key FROM llm_logs WHERE fase = 'PILOTO'")
    return {row["signal_group_key"] for row in rows}


async def get_measurement_signal_group_keys(
    db: Database, prompt_sha256: str | None = None
) -> set[str]:
    """Grupos de la medicion (`fase = 'MEDICION'`), para que `ai_value_verdict`
    (parametro `measurement_keys`) no cuente sombras que nunca pasaron por el
    LLM de esta medicion (subfase 3.6, ajuste de la fase ii; seccion k).

    Si se omite `prompt_sha256`, se usa el mas reciente que aparece con
    `fase='MEDICION'` -- asi dos versiones del prompt (congelacion rota y
    corregida) no se mezclan en el mismo veredicto; la version anterior queda
    en `llm_logs` como historial, pero fuera de `measurement_keys`."""
    if prompt_sha256 is None:
        row = await db.fetch_one(
            "SELECT prompt_sha256 FROM llm_logs WHERE fase = 'MEDICION' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if row is None:
            return set()
        prompt_sha256 = row["prompt_sha256"]
    rows = await db.fetch_all(
        "SELECT signal_group_key FROM llm_logs WHERE fase = 'MEDICION' AND prompt_sha256 = ?",
        (prompt_sha256,),
    )
    return {row["signal_group_key"] for row in rows}
