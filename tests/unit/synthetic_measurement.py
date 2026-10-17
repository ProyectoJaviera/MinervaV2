"""Siembra SINTETICA de una base de medicion para las pruebas del informe de
veredicto (`app.trading.ai_value_report`, `scripts/ai_value_report.py`).

El esquema es el real (`Database.connect()` corre `SCHEMA` y las migraciones);
las filas se insertan en bloque con `sqlite3` porque los repos hacen un commit
por escritura y 600+ grupos tardarian demasiado. Todo lo sembrado lleva
`model == SYNTHETIC_MODEL` y un hash propio (nunca el congelado real): es lo que
`assert_synthetic_database` exige para aceptar la bandera de pruebas."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.persistence.database import Database
from app.trading.ai_value_report import SYNTHETIC_MODEL

SYNTHETIC_HASH = "synthetic-frozen-hash"
BASE = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass
class Group:
    key: str
    opened_at: datetime
    closed_at: datetime | None = None
    symbol: str = "AAAUSDT"
    side: str = "LONG"
    strategy: str = "ema_cross_9_21"
    contributing: tuple[str, ...] = ("ema_cross_9_21",)
    margin: float = 5.0
    ret: float | None = 0.0
    label: str = "APROBADA"
    candle_close_time: datetime | None = None
    reconciliation_failed: bool = False
    close_reason: str = "TP"
    # log: None = grupo omitido (sin fila en llm_logs)
    log: bool = True
    fase: str = "MEDICION"
    prompt_hash: str = SYNTHETIC_HASH
    model: str = SYNTHETIC_MODEL
    status: str = "OK"
    json_repaired: bool = False
    hour_utc: int | None = None
    atr_pct: float | None = 1.0


async def create_schema(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.close()


def insert_groups(path: Path, groups: list[Group]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for g in groups:
            candle = g.candle_close_time or g.opened_at
            status = "CLOSED" if g.closed_at is not None else "OPEN"
            pnl = None if g.ret is None or g.closed_at is None else g.ret * g.margin
            cursor = conn.execute(
                """
                INSERT INTO shadow_trades (
                    symbol, side, strategy, contributing_strategies, signal_group_key,
                    candle_close_time, status, leverage, margin_usdt, notional_usdt, qty,
                    entry_price, opened_at, closed_at, pnl_gross_usdt, pnl_net_usdt, close_reason,
                    llm_decision, reconciliation_failed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 10, ?, ?, 1.0, 100.0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    g.symbol, g.side, g.strategy, _json_list(g.contributing), g.key,
                    candle.isoformat(), status, g.margin, g.margin * 10, g.opened_at.isoformat(),
                    g.closed_at.isoformat() if g.closed_at else None, pnl, pnl,
                    g.close_reason if g.closed_at else None, g.label,
                    int(g.reconciliation_failed),
                ),
            )
            if not g.log:
                continue
            decision = {"APROBADA": "APROBAR", "RECHAZADA": "RECHAZAR"}.get(g.label)
            conn.execute(
                """
                INSERT INTO llm_logs (
                    created_at, signal_group_key, shadow_trade_id, fase, candle_close_time,
                    decision_delay_s, hour_utc, atr_pct, model, prompt_version, prompt_sha256,
                    prompt, response_raw, status, error, decision, input_tokens, output_tokens,
                    cost_usd, latency_ms, features_version, json_repaired
                ) VALUES (?, ?, ?, ?, ?, 5.0, ?, ?, ?, 'signal_review_v4', ?, 'p', NULL, ?, NULL,
                          ?, 100, 20, 0.001, 500, 'features_v2', ?)
                """,
                (
                    (candle + timedelta(seconds=5)).isoformat(), g.key, cursor.lastrowid,
                    g.fase, candle.isoformat(),
                    candle.hour if g.hour_utc is None else g.hour_utc, g.atr_pct, g.model,
                    g.prompt_hash, g.status, decision if g.status == "OK" else None,
                    int(g.json_repaired),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def insert_eval_marks(path: Path, marks: list[datetime], *, per_mark: int = 1) -> None:
    """Marcas de evaluacion de ciclos (`signals.evaluated_at`) para el ritmo EN MARCHA.
    `per_mark` > 1 imita la rafaga de un ciclo real (varias estrategias en el mismo
    minuto). Cada fila lleva un `candle_close_time` unico (UNIQUE de la tabla)."""
    conn = sqlite3.connect(str(path))
    try:
        for i, mark in enumerate(marks):
            for j in range(per_mark):
                evaluated_at = (mark + timedelta(seconds=j)).isoformat()
                conn.execute(
                    "INSERT INTO signals (symbol, strategy, timeframe, candle_close_time, "
                    "evaluated_at, signal, price_at_eval) VALUES (?, 'ema_cross_9_21', '1h', ?, "
                    "?, 'HOLD', 1.0)",
                    (f"S{j}USDT", f"{mark.isoformat()}#{i}", evaluated_at),
                )
        conn.commit()
    finally:
        conn.close()


def _json_list(values: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(values))


def scenario(
    n_approved: int, n_rejected: int, *, approved_ret: float = 0.20, rejected_ret: float = 0.0,
    prefix: str = "g", strategy: str = "ema_cross_9_21", approved_symbol: str = "AAAUSDT",
    rejected_symbol: str = "BBBUSDT", start_hour: float = 0.0,
) -> list[Group]:
    """`n` grupos por lado, cada uno en su propio intervalo (sin solapes): un
    conglomerado por operacion. El retorno alterna +-0.1 alrededor de la media
    (determinista)."""
    out: list[Group] = []
    for i in range(n_approved):
        noise = 0.1 if i % 2 == 0 else -0.1
        t0 = BASE + timedelta(hours=start_hour + 10 * i)
        out.append(Group(
            key=f"{prefix}-A{i}", opened_at=t0, closed_at=t0 + timedelta(hours=1),
            symbol=approved_symbol, strategy=strategy, contributing=(strategy,),
            ret=approved_ret + noise, label="APROBADA",
        ))
    for i in range(n_rejected):
        noise = 0.1 if i % 2 == 0 else -0.1
        t0 = BASE + timedelta(hours=start_hour + 10 * i + 2)
        out.append(Group(
            key=f"{prefix}-R{i}", opened_at=t0, closed_at=t0 + timedelta(hours=1),
            symbol=rejected_symbol, strategy=strategy, contributing=(strategy,),
            ret=rejected_ret + noise, label="RECHAZADA",
        ))
    return out


def open_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
