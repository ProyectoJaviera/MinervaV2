"""Construccion de la memoria: una entrada INMUTABLE por operacion CERRADA y fiable.

Fuente: sombras cerradas con PnL (cada senal agrupada tiene una; son la poblacion completa, con o
sin ejecucion en la cuenta paper). Se excluyen las de reconciliacion fallida (mismo criterio que
`ai_value`). Cada entrada guarda lo que se sabia AL DECIDIR (instante de cierre de la vela) y el
resultado. Las noticias y el Fear & Greed se leen con el limite de `decision_time` (sin fuga).

Esto LEE resultados (PnL): por eso solo corre con `LESSONS_MEMORY_ENABLED` o desde
`scripts/lessons_report.py`, y nada que produzca se muestra sin `is_outcome_reveal_allowed`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.memory.features import risk_reward
from app.news.assets import base_ticker
from app.persistence.database import Database
from app.persistence.llm_log_fields import atr_value
from app.persistence.models import ShadowTrade
from app.persistence.repositories import memory_repo, news_repo, shadow_repo, signals_repo
from app.persistence.repositories.memory_repo import MemoryEntry

NEWS_WINDOW = timedelta(hours=24)

CLOSED_RELIABLE_SQL = """
SELECT * FROM shadow_trades
WHERE status = 'CLOSED' AND pnl_net_usdt IS NOT NULL AND closed_at IS NOT NULL
  AND reconciliation_failed = 0 AND COALESCE(close_reason, '') != 'RECONCILE_FAILED'
  AND margin_usdt > 0
  AND signal_group_key NOT IN (SELECT signal_group_key FROM memory_entries)
ORDER BY closed_at, id
"""


async def count_closed_reliable(db: Database) -> int:
    """Cuantas sombras cerradas y fiables hay en total. Solo un conteo: no lee PnL.

    Args:
        db: conexion a la base.

    Returns:
        El numero de sombras cerradas con PnL, sin reconciliacion fallida.

    Efectos:
        Lee `shadow_trades`.
    """
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM shadow_trades WHERE status = 'CLOSED' "
        "AND pnl_net_usdt IS NOT NULL AND reconciliation_failed = 0 "
        "AND COALESCE(close_reason, '') != 'RECONCILE_FAILED' AND margin_usdt > 0"
    )
    return row["n"]


def atr_from_log(row) -> float | None:
    """El ATR(14) % que vio el modelo al decidir.

    Es la columna `llm_logs.atr_pct` si esta y, si no, el `volatility_atr14_pct` del mensaje
    guardado en `prompt` (el etiquetador en vivo no rellena la columna). Nunca se inventa.

    Args:
        row: fila con `atr_pct` y `prompt`, o `None`.

    Returns:
        El ATR en porcentaje, o `None`.
    """
    if row is None:
        return None
    return atr_value(row["atr_pct"], row["prompt"])


async def _entry_from_shadow(db: Database, shadow: ShadowTrade) -> MemoryEntry:
    decision_time = shadow.candle_close_time or shadow.opened_at
    strategies = sorted(shadow.contributing_strategies) or [shadow.strategy or "n/d"]
    signal = await signals_repo.get_by_symbol_strategy_candle(
        db, shadow.symbol, strategies[0], decision_time
    )
    log = await db.fetch_one(
        "SELECT atr_pct, prompt FROM llm_logs WHERE signal_group_key = ?",
        (shadow.signal_group_key,),
    )
    items = await news_repo.get_for_context(
        db, as_of=decision_time, since=decision_time - NEWS_WINDOW,
        symbol=base_ticker(shadow.symbol), limit=50,
    )
    sentiments = [i.sentiment for i in items if i.sentiment is not None]
    fear_greed = await news_repo.latest_fear_greed(db, decision_time)
    pnl = shadow.pnl_net_usdt or 0.0
    closed_at = shadow.closed_at or decision_time
    return MemoryEntry(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        symbol=shadow.symbol, side=shadow.side.value, strategy_set="+".join(strategies),
        timeframe=signal.timeframe if signal is not None else None,
        decision_time=decision_time, closed_at=closed_at, hour_utc=decision_time.hour,
        atr_pct=atr_from_log(log),
        risk_reward=risk_reward(shadow.entry_price, shadow.sl_price, shadow.tp_price),
        sl_margin_loss_pct=shadow.sl_margin_loss_pct, llm_label=shadow.llm_decision,
        news_sentiment=sum(sentiments) / len(sentiments) if sentiments else None,
        news_count=len(items), fear_greed=fear_greed["value"] if fear_greed else None,
        pnl_net_usdt=pnl, margin_usdt=shadow.margin_usdt,
        return_pct=pnl / shadow.margin_usdt * 100, outcome="WIN" if pnl > 0 else "LOSS",
        close_reason=shadow.close_reason,
        duration_hours=(closed_at - shadow.opened_at).total_seconds() / 3600,
    )


async def refresh_entries(db: Database, *, now: datetime | None = None) -> int:
    """Agrega a la memoria las sombras cerradas y fiables que todavia no estan.

    Cada entrada es INMUTABLE y guarda lo que se sabia al decidir (hora, ATR, riesgo y
    beneficio, etiqueta del LLM, noticias y Fear & Greed con el limite de `decision_time`) y
    el resultado. Se excluyen las de reconciliacion fallida. LEE resultados (PnL).

    Args:
        db: conexion a la base.
        now: hora de construccion; por defecto, ahora (UTC).

    Returns:
        Cuantas entradas se agregaron.

    Efectos:
        Lee `shadow_trades`, `signals`, `llm_logs`, `news_items`, `fear_greed`; escribe
        `memory_entries`.
    """
    now = now or datetime.now(UTC)
    added = 0
    for row in await db.fetch_all(CLOSED_RELIABLE_SQL):
        entry = await _entry_from_shadow(db, shadow_repo.row_to_shadow(row))
        if await memory_repo.insert_entry(db, entry, built_at=now):
            added += 1
    return added
