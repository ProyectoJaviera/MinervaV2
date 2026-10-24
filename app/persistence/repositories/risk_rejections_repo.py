"""Auditoria de los rechazos del motor de riesgo en vivo (Fase 3, subfase 3.2).

Tabla `risk_rejections`. Nunca se usa para cierres: el motor de riesgo solo bloquea
entradas nuevas (ver `app/trading/risk_engine.py`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import RiskRejection, Side


async def insert_rejection(
    db: Database,
    symbol: str,
    side: Side,
    strategy: str | None,
    reason: str,
    details: dict,
) -> None:
    """Registra una entrada bloqueada por el motor de riesgo, con el motivo y los valores exactos.

    Args:
        db: conexion a la base.
        symbol: simbolo de la entrada rechazada.
        side: direccion.
        strategy: estrategia que la propuso, si hay.
        reason: codigo del motivo (los define `app/trading/risk_engine.py`).
        details: valores que motivaron el rechazo (p. ej. `{"open": 3, "limit": 3}`).

    Efectos:
        Escribe `risk_rejections` (INSERT).
    """
    await db.execute(
        """
        INSERT INTO risk_rejections (created_at, symbol, side, strategy, reason, details_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(), symbol, side.value, strategy, reason,
            json.dumps(details),
        ),
    )


def _row_to_rejection(row) -> RiskRejection:
    return RiskRejection(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        symbol=row["symbol"],
        side=Side(row["side"]),
        strategy=row["strategy"],
        reason=row["reason"],
        details=json.loads(row["details_json"]),
    )


async def get_rejections(
    db: Database, reason: str | None = None, limit: int = 100
) -> list[RiskRejection]:
    """Los rechazos mas recientes, opcionalmente de un solo motivo.

    Args:
        db: conexion a la base.
        reason: si se da, solo ese motivo.
        limit: maximo de filas.

    Returns:
        Lista de `RiskRejection`, el mas reciente primero.

    Efectos:
        Lee `risk_rejections`.
    """
    if reason:
        rows = await db.fetch_all(
            "SELECT * FROM risk_rejections WHERE reason = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (reason, limit),
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM risk_rejections ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        )
    return [_row_to_rejection(r) for r in rows]


async def count_by_reason_since(db: Database, since: datetime) -> dict[str, int]:
    """Rechazos desde `since` agrupados por motivo (solo conteos: no lee los detalles).

    Args:
        db: conexion a la base.
        since: inicio del periodo (inclusive).

    Returns:
        Diccionario `{motivo: filas}`, del mas frecuente al menos frecuente.

    Efectos:
        Lee `risk_rejections`.
    """
    rows = await db.fetch_all(
        "SELECT reason, COUNT(*) AS n FROM risk_rejections WHERE created_at >= ? "
        "GROUP BY reason ORDER BY n DESC",
        (since.isoformat(),),
    )
    return {r["reason"]: r["n"] for r in rows}
