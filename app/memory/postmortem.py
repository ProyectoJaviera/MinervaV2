"""Post-mortem determinista de una racha perdedora de la cuenta paper (SPEC: "Post-mortem automatico
tras N perdidas consecutivas"). La pausa automatica ya existe: es el circuit breaker del motor de
riesgo (`CONSECUTIVE_LOSSES_CIRCUIT_BREAKER`). Esto solo ESCRIBE que tuvieron en comun esas
operaciones, sin LLM. El cuerpo lleva PnL: se guarda siempre, pero se muestra solo con
`is_outcome_reveal_allowed`."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from app.memory.features import hour_bin
from app.persistence.database import Database
from app.persistence.repositories import memory_repo

COMMON_SHARE = 0.75
SCAN_LIMIT = 200


def _common(counter: Counter, total: int) -> str | None:
    if not counter:
        return None
    value, count = counter.most_common(1)[0]
    return value if count / total >= COMMON_SHARE else None


async def build_postmortem(db: Database, *, n_losses: int) -> dict | None:
    """Analiza la racha actual de perdidas consecutivas de la cuenta paper. Determinista, sin LLM.

    Describe que tienen en comun esas operaciones (lado, estrategia, simbolo, motivo de
    cierre, franja horaria) cuando una caracteristica supera el 75 %.

    Args:
        db: conexion a la base.
        n_losses: largo minimo de la racha para producir un informe.

    Returns:
        El informe (con `dedupe_key`), o `None` si la racha es mas corta.

    Efectos:
        Lee `trades` (incluido PnL).
    """
    rows = await db.fetch_all(
        "SELECT * FROM trades WHERE status = 'CLOSED' AND pnl_net_usdt IS NOT NULL "
        "AND COALESCE(close_reason, '') != 'RECONCILE_FAILED' "
        "ORDER BY closed_at DESC, id DESC LIMIT ?", (SCAN_LIMIT,),
    )
    streak = []
    for row in rows:
        if row["pnl_net_usdt"] < 0:
            streak.append(row)
        else:
            break
    if len(streak) < n_losses:
        return None
    total = len(streak)
    sides = Counter(r["side"] for r in streak)
    strategies = Counter(r["strategy"] or "n/d" for r in streak)
    symbols = Counter(r["symbol"] for r in streak)
    reasons = Counter(r["close_reason"] or "n/d" for r in streak)
    bins = Counter(hour_bin(datetime.fromisoformat(r["opened_at"]).astimezone(UTC).hour)
                   for r in streak)
    durations = [
        (datetime.fromisoformat(r["closed_at"]) - datetime.fromisoformat(r["opened_at"]))
        .total_seconds() / 3600
        for r in streak
    ]
    return {
        "losses": total,
        "trade_ids": sorted(r["id"] for r in streak),
        "first_closed_at": min(r["closed_at"] for r in streak),
        "last_closed_at": max(r["closed_at"] for r in streak),
        "total_pnl_net_usdt": round(sum(r["pnl_net_usdt"] for r in streak), 4),
        "funding_paid_usdt": round(sum(r["funding_paid_usdt"] or 0.0 for r in streak), 4),
        "by_side": dict(sides), "by_strategy": dict(strategies), "by_symbol": dict(symbols),
        "by_close_reason": dict(reasons), "by_hour_bin": dict(bins),
        "mean_duration_hours": round(sum(durations) / total, 2),
        "in_common": {
            key: value for key, value in {
                "side": _common(sides, total), "strategy": _common(strategies, total),
                "symbol": _common(symbols, total), "close_reason": _common(reasons, total),
                "hour_bin": _common(bins, total),
            }.items() if value is not None
        },
        "note": "descriptivo; una racha corta no prueba nada por si sola",
        "dedupe_key": f"streak-start:{min(r['id'] for r in streak)}",
    }


async def maybe_postmortem(db: Database, *, n_losses: int, now: datetime | None = None) -> bool:
    """Guarda UN post-mortem por racha, con clave su primera perdida.

    Args:
        db: conexion a la base.
        n_losses: largo minimo de la racha.
        now: hora del informe; por defecto, ahora (UTC).

    Returns:
        True si el informe es nuevo.

    Efectos:
        Lee `trades`; escribe `lessons_reports`.
    """
    body = await build_postmortem(db, n_losses=n_losses)
    if body is None:
        return False
    return await memory_repo.save_report(
        db, kind="POSTMORTEM", body=body, created_at=now or datetime.now(UTC),
        dedupe_key=body["dedupe_key"],
    )
