"""Cache de especificaciones de contrato (tabla `contract_specs_cache`).

Una fila por simbolo con volumen minimo, precisiones, apalancamiento y datos de funding.
La alimenta `app/market/contract_specs.py` y la leen el simulador y el brazo ejecutado.
"""

from __future__ import annotations

from app.persistence.database import Database
from app.persistence.models import ContractSpec


async def upsert_spec(db: Database, spec: ContractSpec) -> None:
    """Guarda la especificacion de un contrato (inserta o reemplaza).

    Args:
        db: conexion a la base.
        spec: la especificacion, con `fetched_at`.

    Efectos:
        Escribe `contract_specs_cache` (UPSERT).
    """
    await db.execute(
        """
        INSERT INTO contract_specs_cache (
            symbol, min_trade_volume, base_precision, quote_precision, min_leverage,
            max_leverage, default_margin_mode, margin_tiers_json, funding_rate,
            funding_interval_hours, next_funding_time, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            min_trade_volume = excluded.min_trade_volume,
            base_precision = excluded.base_precision,
            quote_precision = excluded.quote_precision,
            min_leverage = excluded.min_leverage,
            max_leverage = excluded.max_leverage,
            default_margin_mode = excluded.default_margin_mode,
            margin_tiers_json = excluded.margin_tiers_json,
            funding_rate = excluded.funding_rate,
            funding_interval_hours = excluded.funding_interval_hours,
            next_funding_time = excluded.next_funding_time,
            fetched_at = excluded.fetched_at
        """,
        (
            spec.symbol, spec.min_trade_volume, spec.base_precision, spec.quote_precision,
            spec.min_leverage, spec.max_leverage, spec.default_margin_mode,
            spec.margin_tiers_json, spec.funding_rate, spec.funding_interval_hours,
            spec.next_funding_time, spec.fetched_at.isoformat(),
        ),
    )


async def get_spec(db: Database, symbol: str) -> ContractSpec | None:
    """La especificacion en cache de un simbolo.

    Args:
        db: conexion a la base.
        symbol: simbolo.

    Returns:
        El `ContractSpec`, o `None` si no esta en cache.

    Efectos:
        Lee `contract_specs_cache`.
    """
    row = await db.fetch_one("SELECT * FROM contract_specs_cache WHERE symbol = ?", (symbol,))
    if row is None:
        return None
    from datetime import datetime

    return ContractSpec(
        symbol=row["symbol"],
        min_trade_volume=row["min_trade_volume"],
        base_precision=row["base_precision"],
        quote_precision=row["quote_precision"],
        min_leverage=row["min_leverage"],
        max_leverage=row["max_leverage"],
        default_margin_mode=row["default_margin_mode"],
        margin_tiers_json=row["margin_tiers_json"],
        funding_rate=row["funding_rate"],
        funding_interval_hours=row["funding_interval_hours"],
        next_funding_time=row["next_funding_time"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
    )
