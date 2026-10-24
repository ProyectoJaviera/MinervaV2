"""Tabla `llm_logs` (subfase 3.6): una fila por llamada al LLM sobre una senal agrupada.

La fila se guarda con exito o sin el; `signal_group_key` es UNICA (una sola llamada por
grupo, sin reintentos). `fase` separa el piloto (excluido del analisis) de la medicion.
Ver `docs/FASE3_6_LLM.md`, seccion (c).
"""

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
    """Guarda la fila de una llamada al LLM, exitosa o no.

    Una fila por `signal_group_key` (UNIQUE). `features_version` y `json_repaired` vienen de
    la revision del piloto v2 (ver `_LLM_LOGS_MIGRATED_COLUMNS` en
    `app/persistence/database.py`).

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada (identifica la sombra).
        shadow_trade_id: `shadow_trades.id` asociado, si lo hay.
        fase: `PILOTO` o `MEDICION`.
        candle_close_time: cierre de la vela evaluada.
        decision_delay_s: segundos entre el cierre de la vela y la decision.
        hour_utc: hora UTC del cierre de la vela.
        atr_pct: ATR como porcentaje del precio, si se paso (el etiquetador en vivo no lo
            pasa; ver `app/persistence/llm_log_fields.py`).
        model: id del modelo usado.
        prompt_version: version del prompt (p. ej. `signal_review_v4`).
        prompt_sha256: hash combinado del prompt y de la version de las features.
        prompt: mensaje completo enviado (sistema y usuario).
        response_raw: respuesta cruda del modelo, o `None` si no hubo.
        status: `OK`, `TIMEOUT`, `ERROR_HTTP`, `INVALID` o `BUDGET_EXCEEDED`.
        error: texto del error, si lo hubo.
        decision: `APROBAR` o `RECHAZAR`, o `None` si no hubo decision valida.
        input_tokens: tokens de entrada facturados.
        output_tokens: tokens de salida facturados.
        cost_usd: costo estimado de la llamada en USD.
        latency_ms: latencia de la llamada en milisegundos.
        created_at: hora de la fila; por defecto, ahora (UTC).
        features_version: version de las features (p. ej. `features_v2`).
        json_repaired: True si la respuesta se recupero con el parser tolerante.

    Returns:
        El id de la fila insertada.

    Raises:
        sqlite3.IntegrityError: si ya hay una fila para ese `signal_group_key`.

    Efectos:
        Escribe `llm_logs` (INSERT). Sin red.
    """
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
    """Suma el costo (`cost_usd`) de las filas creadas desde `since`.

    Alimenta el gasto del dia (seccion e); el llamador calcula el inicio del dia local con
    `app.llm.budget.local_day_start_utc`. `LlmBudgetTracker.try_reserve` la llama SIN `fase`
    a proposito (el presupuesto diario es global, no por fase); `fase` sirve al bloque
    `llm_labeling` de `GET /health`.

    Args:
        db: conexion a la base.
        since: inicio del periodo (inclusive).
        fase: si se da, solo esa fase.

    Returns:
        Gasto en USD (0.0 si no hay filas).

    Efectos:
        Lee `llm_logs`.
    """
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
    """Cuenta las filas por `status` desde `since`.

    Distingue `BUDGET_EXCEEDED` de `TIMEOUT`, `ERROR_HTTP` e `INVALID`: todas dejan la
    sombra en SIN_LLM, pero por razones distintas. Lo usa `GET /health` (bloque
    `llm_labeling`).

    Args:
        db: conexion a la base.
        since: inicio del periodo (inclusive).
        fase: si se da, solo esa fase.

    Returns:
        Diccionario `{status: filas}`.

    Efectos:
        Lee `llm_logs`.
    """
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
    """Hora de la PRIMERA fila con ese `status` desde `since`.

    Sirve para registrar el momento en que se agoto el presupuesto diario
    (`status = 'BUDGET_EXCEEDED'`).

    Args:
        db: conexion a la base.
        since: inicio del periodo (inclusive).
        status: estado buscado.
        fase: si se da, solo esa fase.

    Returns:
        El `created_at` mas antiguo, o `None` si no hay ninguna fila.

    Efectos:
        Lee `llm_logs`.
    """
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
    """La fila de `llm_logs` de una senal agrupada.

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada.

    Returns:
        La fila `aiosqlite.Row` completa (incluye el prompt), o `None`.

    Efectos:
        Lee `llm_logs`.
    """
    return await db.fetch_one(
        "SELECT * FROM llm_logs WHERE signal_group_key = ?", (signal_group_key,)
    )


async def get_recent(db: Database, limit: int = 50, fase: str | None = None):
    """Las `limit` filas mas recientes, opcionalmente de una sola fase.

    Subfase C del plan pre-Fase 5 (`GET /api/llm/decisions/recent`); mismo patron que
    `trades_repo.get_trades`.

    Args:
        db: conexion a la base.
        limit: maximo de filas.
        fase: si se da, solo `PILOTO` o `MEDICION`.

    Returns:
        Lista de filas `aiosqlite.Row`, la mas reciente primero.

    Efectos:
        Lee `llm_logs`.
    """
    query = "SELECT * FROM llm_logs"
    params: list = []
    if fase is not None:
        query += " WHERE fase = ?"
        params.append(fase)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return await db.fetch_all(query, tuple(params))


async def get_piloto_signal_group_keys(db: Database) -> set[str]:
    """Claves de los grupos del piloto (`fase = 'PILOTO'`), excluidos de la medicion.

    `app.trading.ai_value.ai_value_verdict` los filtra con esto antes de calcular el
    veredicto (subfase 3.6, seccion k).

    Args:
        db: conexion a la base.

    Returns:
        Conjunto de `signal_group_key`.

    Efectos:
        Lee `llm_logs`.
    """
    rows = await db.fetch_all("SELECT signal_group_key FROM llm_logs WHERE fase = 'PILOTO'")
    return {row["signal_group_key"] for row in rows}


async def get_measurement_signal_group_keys(
    db: Database, prompt_sha256: str | None = None
) -> set[str]:
    """Claves de los grupos de la medicion (`fase = 'MEDICION'`) de UN hash de prompt.

    Permite que `ai_value_verdict` (parametro `measurement_keys`) no cuente sombras que
    nunca pasaron por el LLM de esta medicion. Si se omite `prompt_sha256` se usa el mas
    reciente que aparece con `fase = 'MEDICION'`: dos versiones del prompt (congelacion rota
    y corregida) no se mezclan en el mismo veredicto; la anterior queda en `llm_logs` como
    historial pero fuera de `measurement_keys`.

    Args:
        db: conexion a la base.
        prompt_sha256: hash del prompt a seleccionar; `None` = el de la fila mas reciente.

    Returns:
        Conjunto de `signal_group_key` (vacio si no hay filas de medicion).

    Efectos:
        Lee `llm_logs`.
    """
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
