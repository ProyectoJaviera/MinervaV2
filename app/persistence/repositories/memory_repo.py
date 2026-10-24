"""Memoria de lecciones (Fase 4, `app/memory/`).

Tablas: `memory_entries`, `lessons`, `lesson_applications` y `lessons_reports`. Las filas
llevan PnL y tasas de acierto: NADA de lo que lee este repositorio se muestra por API,
Telegram o dashboard sin `is_outcome_reveal_allowed` (ver `app/memory/` y
`app/api/routes/memory.py`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.persistence.database import Database
from app.persistence.repositories.timeutil import iso_utc


@dataclass
class MemoryEntry:
    """Una operacion cerrada convertida en entrada de memoria (fila de `memory_entries`).

    Es INMUTABLE: guarda lo que se sabia al decidir (hora, ATR, riesgo/beneficio, etiqueta
    del LLM, contexto de noticias) y el resultado (`pnl_net_usdt`, `return_pct`, `outcome`).
    """
    signal_group_key: str
    shadow_trade_id: int | None
    symbol: str
    side: str
    strategy_set: str
    timeframe: str | None
    decision_time: datetime
    closed_at: datetime
    hour_utc: int | None
    atr_pct: float | None
    risk_reward: float | None
    sl_margin_loss_pct: float | None
    llm_label: str
    news_sentiment: float | None
    news_count: int | None
    fear_greed: int | None
    pnl_net_usdt: float
    margin_usdt: float
    return_pct: float
    outcome: str
    close_reason: str | None
    duration_hours: float | None
    id: int | None = None

    @property
    def strategies(self) -> frozenset[str]:
        """El conjunto de estrategias de `strategy_set` (separadas por `+`)."""
        return frozenset(s for s in self.strategy_set.split("+") if s)


@dataclass
class LessonRow:
    """Una leccion agregada (fila de `lessons`).

    `status` es `ACTIVE` solo con muestra minima, intervalo que excluye la tasa base y signo
    estable en las dos mitades del tiempo; si no, `INSUFFICIENT`, `UNSTABLE` o
    `NOT_SIGNIFICANT`.
    """
    dimension: str
    pattern: str
    n: int
    wins: int
    winrate: float | None
    ci_low: float | None
    ci_high: float | None
    base_winrate: float | None
    mean_return_pct: float | None
    half1_delta: float | None
    half2_delta: float | None
    status: str
    build_id: str = ""
    built_at: datetime | None = None
    id: int | None = None


def _row_to_entry(row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"], signal_group_key=row["signal_group_key"],
        shadow_trade_id=row["shadow_trade_id"], symbol=row["symbol"], side=row["side"],
        strategy_set=row["strategy_set"], timeframe=row["timeframe"],
        decision_time=datetime.fromisoformat(row["decision_time"]),
        closed_at=datetime.fromisoformat(row["closed_at"]), hour_utc=row["hour_utc"],
        atr_pct=row["atr_pct"], risk_reward=row["risk_reward"],
        sl_margin_loss_pct=row["sl_margin_loss_pct"], llm_label=row["llm_label"],
        news_sentiment=row["news_sentiment"], news_count=row["news_count"],
        fear_greed=row["fear_greed"], pnl_net_usdt=row["pnl_net_usdt"],
        margin_usdt=row["margin_usdt"], return_pct=row["return_pct"], outcome=row["outcome"],
        close_reason=row["close_reason"], duration_hours=row["duration_hours"],
    )


async def insert_entry(db: Database, entry: MemoryEntry, *, built_at: datetime) -> bool:
    """Guarda una entrada de memoria, salvo que su grupo ya tenga una (son inmutables).

    Args:
        db: conexion a la base.
        entry: la entrada.
        built_at: cuando se construyo.

    Returns:
        True si se inserto; False si `signal_group_key` ya existia.

    Efectos:
        Escribe `memory_entries` (INSERT OR IGNORE).
    """
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO memory_entries (
            signal_group_key, shadow_trade_id, built_at, symbol, side, strategy_set, timeframe,
            decision_time, closed_at, hour_utc, atr_pct, risk_reward, sl_margin_loss_pct,
            llm_label, news_sentiment, news_count, fear_greed, pnl_net_usdt, margin_usdt,
            return_pct, outcome, close_reason, duration_hours
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.signal_group_key, entry.shadow_trade_id, iso_utc(built_at), entry.symbol,
            entry.side, entry.strategy_set, entry.timeframe, iso_utc(entry.decision_time),
            iso_utc(entry.closed_at), entry.hour_utc, entry.atr_pct, entry.risk_reward,
            entry.sl_margin_loss_pct, entry.llm_label, entry.news_sentiment, entry.news_count,
            entry.fear_greed, entry.pnl_net_usdt, entry.margin_usdt, entry.return_pct,
            entry.outcome, entry.close_reason, entry.duration_hours,
        ),
    )
    return cursor.rowcount == 1


async def count_entries(db: Database) -> int:
    """Cuantas entradas de memoria hay.

    Args:
        db: conexion a la base.

    Returns:
        El numero de filas de `memory_entries`.

    Efectos:
        Lee `memory_entries`.
    """
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM memory_entries")
    return row["n"]


async def get_known_keys(db: Database) -> set[str]:
    """Las claves de grupo que ya tienen entrada de memoria.

    Args:
        db: conexion a la base.

    Returns:
        Conjunto de `signal_group_key`.

    Efectos:
        Lee `memory_entries`.
    """
    rows = await db.fetch_all("SELECT signal_group_key FROM memory_entries")
    return {r["signal_group_key"] for r in rows}


async def get_entries_closed_before(
    db: Database, cutoff: datetime | None = None,
) -> list[MemoryEntry]:
    """Entradas cerradas ESTRICTAMENTE antes de `cutoff`, las mas antiguas primero.

    Args:
        db: conexion a la base.
        cutoff: limite exclusivo; `None` = todas.

    Returns:
        Lista de `MemoryEntry` por `closed_at` e `id`.

    Efectos:
        Lee `memory_entries` (incluye PnL: no exponerlo sin el registro oficial).
    """
    if cutoff is None:
        rows = await db.fetch_all("SELECT * FROM memory_entries ORDER BY closed_at, id")
    else:
        rows = await db.fetch_all(
            "SELECT * FROM memory_entries WHERE closed_at < ? ORDER BY closed_at, id",
            (iso_utc(cutoff),),
        )
    return [_row_to_entry(r) for r in rows]


async def replace_lessons(
    db: Database, build_id: str, built_at: datetime, lessons: list[LessonRow],
) -> None:
    """Guarda una reconstruccion completa de lecciones bajo `build_id`.

    Las reconstrucciones anteriores se conservan; solo se reemplaza una fila si coincide
    `(build_id, dimension, pattern)`.

    Args:
        db: conexion a la base.
        build_id: identificador de la reconstruccion.
        built_at: cuando se construyo.
        lessons: las lecciones de esa reconstruccion.

    Efectos:
        Escribe `lessons` (INSERT OR REPLACE en bloque).
    """
    await db.execute_many(
        """
        INSERT OR REPLACE INTO lessons (
            build_id, built_at, dimension, pattern, n, wins, winrate, ci_low, ci_high,
            base_winrate, mean_return_pct, half1_delta, half2_delta, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                build_id, built_at.isoformat(), x.dimension, x.pattern, x.n, x.wins, x.winrate,
                x.ci_low, x.ci_high, x.base_winrate, x.mean_return_pct, x.half1_delta,
                x.half2_delta, x.status,
            )
            for x in lessons
        ],
    )


async def latest_build_id(db: Database) -> str | None:
    """El `build_id` de la reconstruccion mas reciente.

    Args:
        db: conexion a la base.

    Returns:
        El identificador, o `None` si nunca se construyeron lecciones.

    Efectos:
        Lee `lessons`.
    """
    row = await db.fetch_one("SELECT build_id FROM lessons ORDER BY id DESC LIMIT 1")
    return row["build_id"] if row else None


async def get_lessons(
    db: Database, build_id: str | None = None, status: str | None = None,
) -> list[LessonRow]:
    """Las lecciones de una reconstruccion, opcionalmente de un solo estado.

    Args:
        db: conexion a la base.
        build_id: reconstruccion a leer; `None` = la mas reciente.
        status: si se da, solo ese estado (p. ej. `ACTIVE`).

    Returns:
        Lista de `LessonRow` por muestra descendente (vacia si no hay reconstruccion).

    Efectos:
        Lee `lessons`.
    """
    build_id = build_id or await latest_build_id(db)
    if build_id is None:
        return []
    query = "SELECT * FROM lessons WHERE build_id = ?"
    params: list = [build_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    rows = await db.fetch_all(query + " ORDER BY n DESC, id", tuple(params))
    return [
        LessonRow(
            id=r["id"], build_id=r["build_id"], built_at=datetime.fromisoformat(r["built_at"]),
            dimension=r["dimension"], pattern=r["pattern"], n=r["n"], wins=r["wins"],
            winrate=r["winrate"], ci_low=r["ci_low"], ci_high=r["ci_high"],
            base_winrate=r["base_winrate"], mean_return_pct=r["mean_return_pct"],
            half1_delta=r["half1_delta"], half2_delta=r["half2_delta"], status=r["status"],
        )
        for r in rows
    ]


async def record_application(
    db: Database, *, signal_group_key: str, created_at: datetime, build_id: str | None,
    lesson_ids: list[int], n_similar: int, evidence: str,
) -> bool:
    """Registra que lecciones se inyectaron en una decision (auditoria de su influencia).

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada.
        created_at: hora del registro.
        build_id: reconstruccion usada, si hubo.
        lesson_ids: ids de las lecciones aplicadas.
        n_similar: operaciones similares encontradas.
        evidence: nivel de evidencia (`BAJA`, `SUFICIENTE`, ...).

    Returns:
        True si se inserto; False si ya habia registro de ese grupo.

    Efectos:
        Escribe `lesson_applications` (INSERT OR IGNORE).
    """
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO lesson_applications (
            signal_group_key, created_at, build_id, lesson_ids_json, n_similar, evidence
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            signal_group_key, created_at.isoformat(), build_id, json.dumps(lesson_ids),
            n_similar, evidence,
        ),
    )
    return cursor.rowcount == 1


async def count_applications(db: Database) -> int:
    """Cuantas aplicaciones de lecciones hay registradas.

    Args:
        db: conexion a la base.

    Returns:
        El numero de filas de `lesson_applications`.

    Efectos:
        Lee `lesson_applications`.
    """
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM lesson_applications")
    return row["n"]


async def save_report(
    db: Database, *, kind: str, body: dict, created_at: datetime, dedupe_key: str | None = None,
) -> bool:
    """Guarda un informe de la memoria (post-mortem o calibracion).

    Args:
        db: conexion a la base.
        kind: `POSTMORTEM` o `CALIBRATION`.
        body: contenido del informe (se guarda como JSON).
        created_at: hora del informe.
        dedupe_key: clave para no repetir el mismo informe, o `None`.

    Returns:
        True si se inserto; False si `dedupe_key` ya existia.

    Efectos:
        Escribe `lessons_reports` (INSERT OR IGNORE).
    """
    cursor = await db.execute(
        "INSERT OR IGNORE INTO lessons_reports (kind, created_at, dedupe_key, body_json) "
        "VALUES (?, ?, ?, ?)",
        (kind, created_at.isoformat(), dedupe_key, json.dumps(body)),
    )
    return cursor.rowcount == 1


async def get_reports(db: Database, kind: str, limit: int = 10) -> list[dict]:
    """Los informes mas recientes de un tipo.

    Args:
        db: conexion a la base.
        kind: `POSTMORTEM` o `CALIBRATION`.
        limit: maximo de informes.

    Returns:
        Lista de diccionarios con `created_at`, `dedupe_key` y `body` (ya decodificado).

    Efectos:
        Lee `lessons_reports`.
    """
    rows = await db.fetch_all(
        "SELECT created_at, dedupe_key, body_json FROM lessons_reports WHERE kind = ? "
        "ORDER BY id DESC LIMIT ?",
        (kind, limit),
    )
    return [
        {"created_at": r["created_at"], "dedupe_key": r["dedupe_key"],
         "body": json.loads(r["body_json"])}
        for r in rows
    ]
