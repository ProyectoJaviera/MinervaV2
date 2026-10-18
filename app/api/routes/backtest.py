"""Veredictos de backtest por estrategia para el dashboard (Fase 5): lo que la Fase 2 decidio con
datos historicos (fuera de muestra, walk-forward, con estres de comisiones) y si cada estrategia es
elegible hoy para la cuenta paper. Es un historico de backtest, NO la medicion en vivo: no pasa por
la puerta de resultados. Solo lectura; protegido con `require_auth` a nivel de router."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends

from app.api.deps import get_db, get_settings
from app.api.security import require_auth
from app.config import Settings
from app.persistence.database import Database
from app.persistence.models import BacktestVerdict
from app.persistence.repositories import backtest_repo

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

NOTE = (
    "Backtest historico de la Fase 2 (fuera de muestra y walk-forward, con comisiones y funding), "
    "no la medicion en vivo del LLM. Una estrategia descartada igual puede aparecer en la cuenta "
    "paper si esta en la lista de elegibles."
)
_LONG_FLOAT = re.compile(r"\d+\.\d{3,}")


def round_reason(text: str) -> str:
    """Los motivos de descarte traen floats crudos (1.1233661231946566): van a 2 decimales."""
    return _LONG_FLOAT.sub(lambda m: f"{float(m.group()):.2f}", text)


def parse_reasons(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        reasons = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(reasons, list):
        return []
    return [round_reason(r) for r in reasons if isinstance(r, str)]


def verdict_to_dict(v: BacktestVerdict, eligible: list[str]) -> dict:
    return {
        "strategy": v.strategy,
        "experimental": v.is_experimental,
        "discarded": v.discarded,
        "evidence_insufficient": v.evidence_insufficient,
        "eligible_for_account": not eligible or v.strategy in eligible,
        "reasons": parse_reasons(v.discard_reasons_json),
        "pf_oos_aggregate": v.pf_oos_aggregate,
        "pf_stressed": v.pf_stressed,
        "pct_symbols_pf_gt1": v.pct_symbols_pf_gt1,
        "pct_folds_positive": v.pct_folds_positive,
        "max_drawdown_oos_pct": v.max_drawdown_oos_pct,
        "concentration_pct": v.concentration_pct,
        "combos_tested": v.combos_tested,
        "total_trades_all_segments": v.total_trades_all_segments,
        "run_at": v.run_at.isoformat(),
    }


@router.get("/backtest/verdicts")
async def verdicts(
    db: Database = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    eligible = settings.real_account_eligible_strategies_list
    rows = await backtest_repo.get_verdicts(db)
    latest = max((v.run_at for v in rows), default=None)
    return {
        "note": NOTE,
        "eligible_strategies": eligible,
        "all_strategies_eligible": not eligible,
        "run_at": latest.isoformat() if latest else None,
        "verdicts": [verdict_to_dict(v, eligible) for v in rows],
    }
