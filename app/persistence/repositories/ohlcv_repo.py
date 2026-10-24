"""Cache local de velas OHLCV y sus marcas de integridad.

Tablas: `ohlcv_cache` (las velas), `ohlcv_floor` (la vela mas antigua que existe en
Bitunix), `ohlcv_series_complete` (marca afirmativa de "serie descargada completa") y
`ohlcv_unrepairable_gaps` (huecos que ya se intento reparar). Permite paginar la
descarga historica de Bitunix (maximo 200 velas por llamada, 10 pedidos por segundo)
sin volver a pedir velas ya guardadas.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import OHLCVBar


async def upsert_bars(db: Database, bars: list[OHLCVBar]) -> None:
    """Inserta o actualiza un lote de velas en una sola transaccion.

    Un commit por fila era el cuello de botella real de la descarga historica (ver
    `Database.execute_many`).

    Args:
        db: conexion a la base.
        bars: velas a guardar (clave: simbolo, intervalo, tipo de precio, `open_time`).

    Efectos:
        Escribe `ohlcv_cache` (UPSERT en bloque).
    """
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
    """Las velas guardadas de una serie, en orden cronologico.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo (`1h`, `4h`, `1d`, ...).
        price_type: tipo de precio de la serie.
        start_time: si se da, solo velas con `open_time >=` (ms Unix).
        end_time: si se da, solo velas con `open_time <=` (ms Unix).

    Returns:
        Lista de `OHLCVBar` ordenada por `open_time`.

    Efectos:
        Lee `ohlcv_cache`.
    """
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
    """Todos los `open_time` guardados de una serie.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.

    Returns:
        Conjunto de `open_time` (ms Unix).

    Efectos:
        Lee `ohlcv_cache`.
    """
    rows = await db.fetch_all(
        "SELECT open_time FROM ohlcv_cache WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return {r["open_time"] for r in rows}


async def get_bar_count(
    db: Database, symbol: str, interval: str, price_type: str, start_time: int, end_time: int
) -> int:
    """Cuenta las velas realmente guardadas en un rango.

    Sirve para detectar huecos internos (velas faltantes dentro de un rango que se considera
    cubierto), comparando contra el numero esperado segun el paso del intervalo.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.
        start_time: inicio del rango (ms Unix, inclusive).
        end_time: fin del rango (ms Unix, inclusive).

    Returns:
        Numero de velas en `[start_time, end_time]`.

    Efectos:
        Lee `ohlcv_cache`.
    """
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM ohlcv_cache WHERE symbol = ? AND interval = ? "
        "AND price_type = ? AND open_time >= ? AND open_time <= ?",
        (symbol, interval, price_type, start_time, end_time),
    )
    return int(row["n"]) if row else 0


async def get_covered_range(
    db: Database, symbol: str, interval: str, price_type: str
) -> tuple[int, int] | None:
    """El `open_time` mas antiguo y el mas nuevo ya guardados de una serie.

    Mas eficiente que `get_covered_open_times` para decidir que rango falta descargar: no
    carga todos los timestamps, solo MIN y MAX.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.

    Returns:
        La tupla `(mas antiguo, mas nuevo)` en ms Unix, o `None` si no hay velas.

    Efectos:
        Lee `ohlcv_cache`.
    """
    row = await db.fetch_one(
        "SELECT MIN(open_time) AS lo, MAX(open_time) AS hi FROM ohlcv_cache "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    if row is None or row["lo"] is None:
        return None
    return int(row["lo"]), int(row["hi"])


async def get_floor(db: Database, symbol: str, interval: str, price_type: str) -> int | None:
    """Timestamp de la vela mas antigua que existe en Bitunix para una serie, si ya se detecto.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.

    Returns:
        El `floor_open_time` (ms Unix), o `None` si aun no se detecto (ver `set_floor`).

    Efectos:
        Lee `ohlcv_floor`.
    """
    row = await db.fetch_one(
        "SELECT floor_open_time FROM ohlcv_floor "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return int(row["floor_open_time"]) if row else None


async def set_floor(
    db: Database, symbol: str, interval: str, price_type: str, floor_open_time: int
) -> None:
    """Recuerda el piso del historial de una serie (conserva el mas antiguo).

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.
        floor_open_time: `open_time` de la vela mas antigua disponible (ms Unix).

    Efectos:
        Escribe `ohlcv_floor` (UPSERT con `MIN`).
    """
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
    """Marca que un rango de la serie se descargo completo, sin errores.

    La escribe `scripts/download_history.py` al terminar cada serie: es una senal afirmativa
    independiente de si `ohlcv_floor` tiene fila (ver la tabla en `database.py`). Amplia el
    rango si ya habia una marca.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.
        start_time: inicio del rango completo (ms Unix).
        end_time: fin del rango completo (ms Unix).

    Efectos:
        Escribe `ohlcv_series_complete` (UPSERT con `MIN` y `MAX`).
    """
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
    """Los huecos que ya se confirmaron irreparables (ni con un pedido estrecho).

    Evita que `_verify_and_repair` los reintente en cada ciclo (incidente de estabilidad
    2026-10-10; `GAP_RETRY_COOLDOWN_HOURS` en `app/market/ohlcv_history.py`).

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.

    Returns:
        Diccionario `{open_time: ultimo intento}`.

    Efectos:
        Lee `ohlcv_unrepairable_gaps`.
    """
    rows = await db.fetch_all(
        "SELECT open_time, last_attempted_at FROM ohlcv_unrepairable_gaps "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return {r["open_time"]: datetime.fromisoformat(r["last_attempted_at"]) for r in rows}


async def mark_unrepairable_gap(
    db: Database, symbol: str, interval: str, price_type: str, open_time: int, now: datetime,
) -> None:
    """Registra (o suma un intento a) un hueco que no se pudo reparar.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.
        open_time: `open_time` de la vela faltante (ms Unix).
        now: instante del intento.

    Efectos:
        Escribe `ohlcv_unrepairable_gaps` (UPSERT: `last_attempted_at`, `attempts + 1`).
    """
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
    """Borra la marca de un hueco que un reintento si logro recuperar.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.
        open_time: `open_time` de la vela recuperada (ms Unix).

    Efectos:
        Escribe `ohlcv_unrepairable_gaps` (DELETE).
    """
    await db.execute(
        "DELETE FROM ohlcv_unrepairable_gaps "
        "WHERE symbol = ? AND interval = ? AND price_type = ? AND open_time = ?",
        (symbol, interval, price_type, open_time),
    )


async def get_series_complete_start(
    db: Database, symbol: str, interval: str, price_type: str
) -> int | None:
    """`start_time` de la marca de "serie completa" de una serie.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        interval: intervalo.
        price_type: tipo de precio.

    Returns:
        El inicio del rango marcado completo (ms Unix), o `None` si nunca se marco. Ver
        `mark_series_complete`.

    Efectos:
        Lee `ohlcv_series_complete`.
    """
    row = await db.fetch_one(
        "SELECT start_time FROM ohlcv_series_complete "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return int(row["start_time"]) if row else None
