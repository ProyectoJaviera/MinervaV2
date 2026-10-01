"""Orquesta la construccion del universo dinamico (Fase 2, docs/FASE2_PLAN.md
seccion D): top-N de CoinGecko por capitalizacion, menos stablecoins/wrapped/
liquid-staking, intersectado con los perpetuos USDT activos de Bitunix, con
lista manual de exclusion y un chequeo de sanidad de precio CoinGecko vs.
Bitunix (punto 6 de los ajustes de Fase 2).

Degradacion (CLAUDE.md: "bloqueo de nuevas operaciones si los datos
criticos estan obsoletos"): si el refresco falla, se conserva el ultimo
snapshot persistido; `is_universe_stale()` indica si ya supero el umbral
configurado.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.market.coingecko_client import CoinGeckoApiError, CoinGeckoClient
from app.persistence.database import Database
from app.persistence.models import AssetUniverseEntry
from app.persistence.repositories import universe_repo

logger = get_logger(__name__)


async def _get_exclusion_map(client: CoinGeckoClient, categories: list[str]) -> dict[str, str]:
    """Devuelve {coingecko_id: category_id} para todas las monedas de las
    categorias a excluir (per_page=250, el maximo -- punto 6 de los ajustes,
    para no dejar fuera monedas de esas categorias por paginacion)."""
    exclusion_map: dict[str, str] = {}
    for category in categories:
        coins = await client.get_markets(per_page=250, category=category)
        for coin in coins:
            coin_id = coin.get("id")
            if coin_id and coin_id not in exclusion_map:
                exclusion_map[coin_id] = category
    return exclusion_map


async def build_universe_snapshot(
    coingecko: CoinGeckoClient,
    bitunix: BitunixRestClient,
    settings: Settings,
) -> list[AssetUniverseEntry]:
    """Construye (sin persistir) el snapshot completo -- incluye TODOS los
    candidatos evaluados (no solo los incluidos), para que la exclusion
    quede auditable."""
    now = datetime.now(UTC)

    candidates = await coingecko.get_markets(per_page=settings.universe_candidate_pool)
    exclusion_map = await _get_exclusion_map(coingecko, settings.universe_exclude_categories_list)
    manual_exclusions = set(settings.universe_manual_exclusions_list)

    pairs = await bitunix.get_trading_pairs()
    open_symbols = {p["symbol"] for p in pairs if p.get("symbolStatus") == "OPEN"}

    tickers = await bitunix.get_tickers()
    bitunix_price_by_symbol = {
        t["symbol"]: float(t["lastPrice"]) for t in tickers if t.get("lastPrice") is not None
    }

    entries: list[AssetUniverseEntry] = []
    included_count = 0

    for rank, coin in enumerate(candidates, start=1):
        coin_id = coin.get("id", "")
        proposed_symbol = f"{coin.get('symbol', '').upper()}USDT"
        excluded_category = exclusion_map.get(coin_id)
        excluded_manual = coin_id in manual_exclusions
        has_perp = proposed_symbol in open_symbols

        price_sanity_ok: bool | None = None
        if has_perp and not excluded_category and not excluded_manual:
            cg_price = coin.get("current_price")
            bx_price = bitunix_price_by_symbol.get(proposed_symbol)
            if cg_price and bx_price:
                diff_pct = abs(bx_price - cg_price) / cg_price
                price_sanity_ok = diff_pct <= settings.universe_price_sanity_tolerance_pct
                if not price_sanity_ok:
                    logger.warning(
                        "Sanidad de precio fallida para %s: CoinGecko=%.6f Bitunix=%.6f "
                        "(%.2f%% de diferencia)",
                        proposed_symbol, cg_price, bx_price, diff_pct * 100,
                    )

        included = bool(
            has_perp
            and not excluded_category
            and not excluded_manual
            and price_sanity_ok is not False
            and included_count < settings.universe_size
        )
        if included:
            included_count += 1

        entries.append(
            AssetUniverseEntry(
                refreshed_at=now,
                coingecko_id=coin_id,
                symbol=proposed_symbol,
                coingecko_rank=rank,
                market_cap_usd=coin.get("market_cap"),
                excluded_category=excluded_category,
                excluded_manual=excluded_manual,
                has_bitunix_perp=has_perp,
                price_sanity_ok=price_sanity_ok,
                included=included,
            )
        )

    return entries


async def refresh_universe(
    coingecko: CoinGeckoClient,
    bitunix: BitunixRestClient,
    db: Database,
    settings: Settings,
) -> list[AssetUniverseEntry]:
    """Intenta refrescar el universo; si falla, deja el ultimo snapshot
    persistido intacto (degradacion controlada) y re-lanza la excepcion para
    que el llamador decida como notificar/registrar el fallo."""
    try:
        entries = await build_universe_snapshot(coingecko, bitunix, settings)
    except CoinGeckoApiError:
        logger.exception("Refresco de universo fallido; se conserva el ultimo snapshot valido.")
        raise
    await universe_repo.insert_snapshot(db, entries)
    return entries


async def is_universe_stale(db: Database, settings: Settings) -> bool:
    latest = await universe_repo.get_latest_refreshed_at(db)
    if latest is None:
        return True
    age_hours = (datetime.now(UTC) - latest).total_seconds() / 3600
    return age_hours > settings.universe_staleness_hours
