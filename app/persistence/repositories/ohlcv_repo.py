"""Cache local de velas OHLCV (tabla `ohlcv_cache`).

Permite paginar la descarga historica de Bitunix (max 200 velas/llamada,
10 req/s) sin volver a pedir velas ya guardadas.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import OHLCVBar


async def upsert_bars(db: Database, bars: list[OHLCVBar]) -> None:
    """Una sola transaccion para todo el lote (p.ej. una pagina de 200
    velas) -- un commit por fila era el cuello de botella real de la
    descarga historica (ver Database.execute_many)."""
    params = [
        (
            bar.symbol, bar.interval, bar.price_type, bar.open_time, bar.open,
            bar.high, bar.low, bar.close, bar.base_vol, bar.quote_vol,
        )
        for bar in bars
    ]
    await db.execute_many(
        """
        INSERT INTO ohlcv_cache (
            symbol, interval, price_type, open_time, open, high, low, close,
            base_vol, quote_vol
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, interval, price_type, open_time) DO UPDATE SET
            open = excluded.open, high = excluded.high, low = excluded.low,
            close = excluded.close, base_vol = excluded.base_vol,
            quote_vol = excluded.quote_vol
        """,
        params,
    )


async def get_bars(
    db: Database,
    symbol: str,
    interval: str,
    price_type: str,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[OHLCVBar]:
    query = (
        "SELECT * FROM ohlcv_cache WHERE symbol = ? AND interval = ? AND price_type = ?"
    )
    params: list = [symbol, interval, price_type]
    if start_time is not None:
        query += " AND open_time >= ?"
        params.append(start_time)
    if end_time is not None:
        query += " AND open_time <= ?"
        params.append(end_time)
    query += " ORDER BY open_time ASC"
    rows = await db.fetch_all(query, tuple(params))
    return [
        OHLCVBar(
            symbol=r["symbol"], interval=r["interval"], price_type=r["price_type"],
            open_time=r["open_time"], open=r["open"], high=r["high"], low=r["low"],
            close=r["close"], base_vol=r["base_vol"], quote_vol=r["quote_vol"],
        )
        for r in rows
    ]


async def get_covered_open_times(
    db: Database, symbol: str, interval: str, price_type: str
) -> set[int]:
    rows = await db.fetch_all(
        "SELECT open_time FROM ohlcv_cache WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return {r["open_time"] for r in rows}


async def get_bar_count(
    db: Database, symbol: str, interval: str, price_type: str, start_time: int, end_time: int
) -> int:
    """Cuenta las velas realmente guardadas en [start_time, end_time] --
    usado para detectar huecos internos (velas faltantes dentro de un
    rango que se considera cubierto), comparando contra el numero
    esperado segun el paso del intervalo."""
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM ohlcv_cache WHERE symbol = ? AND interval = ? "
        "AND price_type = ? AND open_time >= ? AND open_time <= ?",
        (symbol, interval, price_type, start_time, end_time),
    )
    return int(row["n"]) if row else 0


async def get_covered_range(
    db: Database, symbol: str, interval: str, price_type: str
) -> tuple[int, int] | None:
    """(open_time mas antiguo, open_time mas nuevo) ya cacheados, o `None`
    si no hay ninguna vela. Mas eficiente que `get_covered_open_times` para
    decidir que rango falta descargar (no carga todos los timestamps a
    memoria, solo MIN/MAX via SQL)."""
    row = await db.fetch_one(
        "SELECT MIN(open_time) AS lo, MAX(open_time) AS hi FROM ohlcv_cache "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    if row is None or row["lo"] is None:
        return None
    return int(row["lo"]), int(row["hi"])


async def get_floor(db: Database, symbol: str, interval: str, price_type: str) -> int | None:
    """Timestamp de la vela mas antigua que existe en Bitunix para este
    (symbol, interval, price_type), si ya se detecto (ver `set_floor`)."""
    row = await db.fetch_one(
        "SELECT floor_open_time FROM ohlcv_floor "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return int(row["floor_open_time"]) if row else None


async def set_floor(
    db: Database, symbol: str, interval: str, price_type: str, floor_open_time: int
) -> None:
    await db.execute(
        """
        INSERT INTO ohlcv_floor (symbol, interval, price_type, floor_open_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (symbol, interval, price_type) DO UPDATE SET
            floor_open_time = MIN(floor_open_time, excluded.floor_open_time)
        """,
        (symbol, interval, price_type, floor_open_time),
    )


async def mark_series_complete(
    db: Database, symbol: str, interval: str, price_type: str, start_time: int, end_time: int,
) -> None:
    """Escrita por `scripts/download_history.py` al terminar una serie SIN
    errores -- senal afirmativa de que [start_time, end_time] quedo
    completo, independiente de si `ohlcv_floor` tiene fila (ver docstring
    de la tabla en `database.py`)."""
    await db.execute(
        """
        INSERT INTO ohlcv_series_complete (
            symbol, interval, price_type, start_time, end_time, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, interval, price_type) DO UPDATE SET
            start_time = MIN(start_time, excluded.start_time),
            end_time = MAX(end_time, excluded.end_time),
            completed_at = excluded.completed_at
        """,
        (symbol, interval, price_type, start_time, end_time, datetime.now(UTC).isoformat()),
    )


async def get_unrepairable_gaps(
    db: Database, symbol: str, interval: str, price_type: str
) -> dict[int, datetime]:
    """`open_time -> last_attempted_at` de los huecos ya confirmados
    irreparables (ni con pedido estrecho) para este simbolo/intervalo/
    price_type -- evita que `_verify_and_repair` los reintente cada ciclo
    (incidente de estabilidad 2026-10-10; `GAP_RETRY_COOLDOWN_HOURS` en
    `app/market/ohlcv_history.py`)."""
    rows = await db.fetch_all(
        "SELECT open_time, last_attempted_at FROM ohlcv_unrepairable_gaps "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return {r["open_time"]: datetime.fromisoformat(r["last_attempted_at"]) for r in rows}


async def mark_unrepairable_gap(
    db: Database, symbol: str, interval: str, price_type: str, open_time: int, now: datetime,
) -> None:
    await db.execute(
        """
        INSERT INTO ohlcv_unrepairable_gaps (
            symbol, interval, price_type, open_time, first_seen_at, last_attempted_at, attempts
        ) VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT (symbol, interval, price_type, open_time) DO UPDATE SET
            last_attempted_at = excluded.last_attempted_at, attempts = attempts + 1
        """,
        (symbol, interval, price_type, open_time, now.isoformat(), now.isoformat()),
    )


async def clear_unrepairable_gap(
    db: Database, symbol: str, interval: str, price_type: str, open_time: int,
) -> None:
    await db.execute(
        "DELETE FROM ohlcv_unrepairable_gaps "
        "WHERE symbol = ? AND interval = ? AND price_type = ? AND open_time = ?",
        (symbol, interval, price_type, open_time),
    )


async def get_series_complete_start(
    db: Database, symbol: str, interval: str, price_type: str
) -> int | None:
    """`start_time` de la marca de "serie completa" mas antigua registrada
    para este (symbol, interval, price_type), o `None` si nunca se marco
    como completa. Ver `mark_series_complete`."""
    row = await db.fetch_one(
        "SELECT start_time FROM ohlcv_series_complete "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return int(row["start_time"]) if row else None
