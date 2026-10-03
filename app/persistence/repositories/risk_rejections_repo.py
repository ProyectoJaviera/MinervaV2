"""Auditoria de rechazos del motor de riesgo en vivo (Fase 3, subfase 3.2)
-- tabla `risk_rejections`. Nunca se usa para cierres: el motor de riesgo
solo bloquea entradas nuevas (ver `app/trading/risk_engine.py`)."""

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
    if reason:
        rows = await db.fetch_all(
            "SELECT * FROM risk_rejections WHERE reason = ? ORDER BY created_at DESC LIMIT ?",
            (reason, limit),
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM risk_rejections ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    return [_row_to_rejection(r) for r in rows]
