"""Calculo del precio de liquidacion en margen aislado (Fase 2, punto 6 de
los ajustes: usa MARK_PRICE, no LAST_PRICE, para evaluar liquidaciones).

Derivacion (margen aislado, aproximando el margen de mantenimiento sobre el
notional de ENTRADA -- simplificacion estandar, documentada, que tambien
usan la mayoria de las calculadoras de margen de los exchanges):

Se liquida cuando margen + PnL_no_realizado <= notional * MMR.
    LONG:  margen + (precio - entrada) * qty = notional * MMR
         -> precio_liq = entrada * (1 - 1/leverage + MMR)
    SHORT: margen + (entrada - precio) * qty = notional * MMR
         -> precio_liq = entrada * (1 + 1/leverage - MMR)

MMR (maintenance margin rate) se toma del tramo de `position_tiers` que
corresponde al notional de la posicion (`contract_specs_cache.margin_tiers_json`,
cacheado desde Fase 1). Si no hay tiers disponibles, se usa un MMR de
respaldo conservador.
"""

from __future__ import annotations

import json

from app.persistence.models import Side

DEFAULT_MMR_FALLBACK = 0.005  # 0.5%, conservador si no hay tiers cacheados


def maintenance_margin_rate_for_notional(margin_tiers_json: str | None, notional: float) -> float:
    if not margin_tiers_json:
        return DEFAULT_MMR_FALLBACK
    try:
        tiers = json.loads(margin_tiers_json)
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_MMR_FALLBACK
    for tier in tiers:
        start = float(tier.get("startValue", 0) or 0)
        end = float(tier.get("endValue", float("inf")) or float("inf"))
        if start <= notional <= end:
            rate = tier.get("maintenanceMarginRate")
            if rate is not None:
                return float(rate)
    return DEFAULT_MMR_FALLBACK


def compute_liquidation_price(
    side: Side, entry_price: float, leverage: int, notional: float, margin_tiers_json: str | None
) -> float:
    mmr = maintenance_margin_rate_for_notional(margin_tiers_json, notional)
    inv_leverage = 1.0 / leverage
    if side == Side.LONG:
        return entry_price * (1 - inv_leverage + mmr)
    return entry_price * (1 + inv_leverage - mmr)
