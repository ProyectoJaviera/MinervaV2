"""Calibracion de la confianza del LLM (SPEC: "Evalua si el porcentaje de exito estimado esta
calibrado"): por tramos de `confianza` (la del esquema `signal_review`, 0-1), cuantas de las
operaciones que el LLM aprobo (o rechazo) terminaron ganando. Es INFORMATIVA: no recalibra ningun
umbral por si sola (decision del usuario, con muestra suficiente y despues del veredicto).

Lleva tasas de acierto: se calcula siempre que se pida, pero se muestra solo con
`is_outcome_reveal_allowed`. Usa las filas ya cerradas de `memory_entries`."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.memory.lessons import wilson_interval
from app.persistence.database import Database
from app.persistence.repositories import memory_repo

# (nombre, desde inclusive, hasta exclusive)
BUCKETS = (
    ("<0.50", 0.0, 0.50), ("0.50-0.60", 0.50, 0.60), ("0.60-0.70", 0.60, 0.70),
    ("0.70-0.80", 0.70, 0.80), ("0.80-0.90", 0.80, 0.90), (">=0.90", 0.90, 1.0001),
)


def confidence_of(response_raw: str | None) -> float | None:
    """La `confianza` del JSON de la respuesta del LLM. Nunca lanza.

    Args:
        response_raw: el texto crudo guardado en `llm_logs.response_raw`.

    Returns:
        La confianza entre 0 y 1, o `None` si no se puede leer.
    """
    if not response_raw:
        return None
    try:
        value = json.loads(response_raw).get("confianza")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


async def calibration_table(db: Database) -> dict:
    """Calibracion de la confianza del LLM: cuantas decisiones de cada tramo terminaron ganando.

    Une `llm_logs` con `memory_entries` y reparte por tramos de confianza, por separado para
    APROBAR y RECHAZAR, con el intervalo de Wilson al 99 %. Es INFORMATIVA: no recalibra
    ningun umbral. Lleva tasas de acierto, asi que se muestra solo con el registro oficial.

    Args:
        db: conexion a la base.

    Returns:
        Diccionario con `rows`, `unreadable_confidence`, `table` y una nota.

    Efectos:
        Lee `llm_logs` y `memory_entries` (incluidos resultados).
    """
    rows = await db.fetch_all(
        """
        SELECT l.decision AS decision, l.response_raw AS raw, m.outcome AS outcome,
               m.return_pct AS return_pct
        FROM llm_logs l JOIN memory_entries m ON m.signal_group_key = l.signal_group_key
        WHERE l.status = 'OK' AND l.decision IN ('APROBAR', 'RECHAZAR')
        """
    )
    table: dict[str, list[dict]] = {"APROBAR": [], "RECHAZAR": []}
    unreadable = 0
    for decision in table:
        for name, low, high in BUCKETS:
            members = []
            for r in rows:
                confidence = confidence_of(r["raw"])
                if confidence is None or r["decision"] != decision:
                    continue
                if low <= confidence < high:
                    members.append(r)
            n = len(members)
            wins = sum(1 for r in members if r["outcome"] == "WIN")
            lo, hi = wilson_interval(wins, n) if n else (None, None)
            table[decision].append({
                "bucket": name, "n": n, "winrate": wins / n if n else None,
                "ci99": [lo, hi] if n else None,
                "mean_return_pct": sum(r["return_pct"] for r in members) / n if n else None,
            })
    unreadable = sum(1 for r in rows if confidence_of(r["raw"]) is None)
    return {
        "rows": len(rows), "unreadable_confidence": unreadable, "table": table,
        "note": "informativa; con n chico un tramo no dice nada (ver ci99)",
    }


async def save_calibration(db: Database, *, now: datetime | None = None) -> bool:
    """Guarda la tabla de calibracion como informe, una vez por cantidad de entradas.

    Args:
        db: conexion a la base.
        now: hora del informe; por defecto, ahora (UTC).

    Returns:
        True si se guardo; False si ya habia un informe con la misma clave (`entries:<n>`).

    Efectos:
        Lee `llm_logs`, `memory_entries`; escribe `lessons_reports`.
    """
    body = await calibration_table(db)
    total = await memory_repo.count_entries(db)
    return await memory_repo.save_report(
        db, kind="CALIBRATION", body=body, created_at=now or datetime.now(UTC),
        dedupe_key=f"entries:{total}",
    )
