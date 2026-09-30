"""Obtiene y cachea especificaciones de contrato por simbolo: `trading_pairs`
(precision, minimos, apalancamiento), `position_tiers` (margen de mantenimiento
por tramo, para liquidacion en Fase 3) y `funding_rate/batch` (funding vigente).

Campos verificados en docs/FASE0.md seccion 2:
- trading_pairs: symbol, minTradeVolume, basePrecision, quotePrecision,
  minLeverage, maxLeverage, defaultMarginMode, symbolStatus, isApiSupported.
- position_tiers: level, startValue, endValue, leverage, maintenanceMarginRate.
- funding_rate/batch: symbol, fundingRate, fundingInterval, nextFundingTime.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.models import ContractSpec
from app.persistence.repositories import specs_repo

logger = get_logger(__name__)


async def refresh_spec(client: BitunixRestClient, db: Database, symbol: str) -> ContractSpec:
    pairs = await client.get_trading_pairs(symbol)
    pair = next((p for p in pairs if p.get("symbol") == symbol), pairs[0] if pairs else {})

    try:
        tiers = await client.get_position_tiers(symbol)
    except Exception as exc:  # noqa: BLE001 - no debe tumbar el refresco por esto
        logger.warning("No se pudieron obtener position_tiers de %s: %s", symbol, exc)
        tiers = []

    funding_all = await client.get_funding_rate_batch()
    funding = next((f for f in funding_all if f.get("symbol") == symbol), None)

    spec = ContractSpec(
        symbol=symbol,
        min_trade_volume=_to_float(pair.get("minTradeVolume")),
        base_precision=_to_int(pair.get("basePrecision")),
        quote_precision=_to_int(pair.get("quotePrecision")),
        min_leverage=_to_int(pair.get("minLeverage")),
        max_leverage=_to_int(pair.get("maxLeverage")),
        default_margin_mode=pair.get("defaultMarginMode"),
        margin_tiers_json=json.dumps(tiers) if tiers else None,
        funding_rate=_to_float(funding.get("fundingRate")) if funding else None,
        funding_interval_hours=_to_int(funding.get("fundingInterval")) if funding else None,
        next_funding_time=_to_int(funding.get("nextFundingTime")) if funding else None,
        fetched_at=datetime.now(UTC),
    )
    await specs_repo.upsert_spec(db, spec)
    return spec


def _to_float(value) -> float | None:
    return float(value) if value is not None else None


def _to_int(value) -> int | None:
    return int(value) if value is not None else None
