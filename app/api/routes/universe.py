"""Universo vigente para el dashboard (Fase 5): el ultimo snapshot de `asset_universe` (los 30
candidatos de CoinGecko con el motivo de cada exclusion), su antiguedad, los cambios recientes y el
ultimo mark que ya tiene el monitor de cada incluido. Lleva la atribucion de CoinGecko
("Data provided by CoinGecko", enlazada: ver `app/market/attribution.py`). Son datos de mercado y
de configuracion: nada de resultados. Solo lectura; protegido con `require_auth` a nivel de
router."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_db, get_monitor, get_settings
from app.api.security import require_auth
from app.config import Settings
from app.market.attribution import COINGECKO_ATTRIBUTION
from app.market.universe import LAST_REFRESH_ERROR_KEY
from app.market.universe_diff import universe_changes
from app.persistence.database import Database
from app.persistence.models import AssetUniverseEntry
from app.persistence.repositories import system_state_repo, universe_repo

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

MAX_CHANGES = 10
RULE = (
    "Un activo que sale del universo con una posicion abierta NO se cierra por la fuerza: solo se "
    "bloquean entradas nuevas y la posicion sigue su ciclo normal (SL/TP/trailing)."
)


def exclusion_reason(entry: AssetUniverseEntry, settings: Settings) -> str | None:
    """Por que un candidato no esta incluido, en el mismo orden en que lo decide el refresco."""
    if entry.included:
        return None
    if entry.excluded_category:
        return f"Categoria excluida: {entry.excluded_category}"
    if entry.excluded_manual:
        return "Excluida manualmente (UNIVERSE_MANUAL_EXCLUSIONS)"
    if not entry.has_bitunix_perp:
        return "Sin perpetuo USDT activo en Bitunix"
    if entry.price_sanity_ok is False:
        return "Fallo la verificacion de precio contra Bitunix"
    return f"Fuera del tamano del universo ({settings.universe_size} activos)"


@router.get("/universe")
async def universe(
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    monitor=Depends(get_monitor),
) -> dict:
    """`GET /api/universe`: el universo vigente con el motivo de cada exclusion, y su atribucion.

    Args:
        db: base (inyectada).
        settings: configuracion (inyectada).
        monitor: monitor de posiciones (inyectado), para el ultimo precio de cada incluido.

    Returns:
        Snapshot, antiguedad, cambios recientes, candidatos y la atribucion de CoinGecko.

    Efectos:
        Lee `asset_universe` y `system_state`.
    """
    snapshot = await universe_repo.get_latest_snapshot(db)
    refreshed_at = snapshot[0].refreshed_at if snapshot else None
    age_hours = (
        None if refreshed_at is None
        else (datetime.now(UTC) - refreshed_at).total_seconds() / 3600
    )
    marks = dict(getattr(monitor, "marks", {}) or {})
    history = await universe_repo.get_included_history(db, limit_snapshots=30)
    return {
        "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        "age_hours": age_hours,
        "stale": age_hours is None or age_hours > settings.universe_staleness_hours,
        "staleness_limit_hours": settings.universe_staleness_hours,
        "refresh_hours": settings.universe_refresh_hours,
        "universe_size": settings.universe_size,
        "last_refresh_error": await system_state_repo.get_state(db, LAST_REFRESH_ERROR_KEY) or None,
        "included": [e.symbol for e in snapshot if e.included],
        "candidates": [
            {
                "rank": e.coingecko_rank,
                "coingecko_id": e.coingecko_id,
                "symbol": e.symbol,
                "market_cap_usd": e.market_cap_usd,
                "included": e.included,
                "excluded_category": e.excluded_category,
                "excluded_manual": e.excluded_manual,
                "has_bitunix_perp": e.has_bitunix_perp,
                "price_sanity_ok": e.price_sanity_ok,
                "exclusion_reason": exclusion_reason(e, settings),
                "mark_price": marks.get(e.symbol) if e.included else None,
            }
            for e in snapshot
        ],
        "changes": [
            {"at": c.at.isoformat(), "added": list(c.added), "removed": list(c.removed)}
            for c in universe_changes(history)[:MAX_CHANGES]
        ],
        "rule": RULE,
        "attribution": COINGECKO_ATTRIBUTION,
    }
