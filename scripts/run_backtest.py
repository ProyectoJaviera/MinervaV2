"""Ejecuta el backtest completo de Fase 2 contra datos reales de Bitunix/
CoinGecko y persiste todo en la base de datos (`asset_universe`,
`backtest_trades`, `backtest_runs`, `backtest_verdicts`).

No hay API/frontend todavia para disparar esto (ver docs/FASE2_PLAN.md,
fuera de alcance de esta fase) -- se corre manualmente:

    python scripts/run_backtest.py

Imprime un resumen por estrategia al terminar. Los criterios y parametros
usados estan fijados y commiteados en docs/FASE2_CRITERIOS.md ANTES de esta
corrida (punto 8 de los ajustes de Fase 2) -- no se deben modificar tras
ver estos resultados.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, ".")

from app.backtesting.report import run_full_backtest  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.market.bitunix_rest import BitunixRestClient  # noqa: E402
from app.market.coingecko_client import CoinGeckoClient  # noqa: E402
from app.market.contract_specs import refresh_spec  # noqa: E402
from app.market.universe import refresh_universe  # noqa: E402
from app.persistence.database import Database  # noqa: E402

logger = get_logger("run_backtest")

# Ancla de inicio anterior al piso real verificado de Bitunix (~2022-04-17):
# la paginacion hacia atras se detiene sola al agotar el historial.
START_MS = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)


async def main() -> None:
    setup_logging()
    t0 = time.time()
    db = Database(settings.database_path)
    await db.connect()
    rest_client = BitunixRestClient(
        base_url=settings.bitunix_rest_base_url,
        rate_limit_per_sec=settings.bitunix_rate_limit_per_sec,
    )
    coingecko_client = CoinGeckoClient(settings.coingecko_base_url, settings.coingecko_api_key)

    try:
        logger.info("Construyendo universo dinamico...")
        universe_entries = await refresh_universe(coingecko_client, rest_client, db, settings)
        universe_symbols = [e.symbol for e in universe_entries if e.included]
        logger.info("Universo incluido: %s", universe_symbols)

        all_symbols = sorted(set(universe_symbols) | set(settings.backtest_control_symbols_list))
        logger.info("Refrescando specs de contrato para %d simbolos...", len(all_symbols))
        for symbol in all_symbols:
            try:
                await refresh_spec(rest_client, db, symbol)
            except Exception:
                logger.exception("No se pudo refrescar specs de %s", symbol)

        end_ms = int(time.time() * 1000)
        logger.info(
            "Corriendo backtest completo (%s a %s)...",
            datetime.fromtimestamp(START_MS / 1000, tz=UTC).date(),
            datetime.fromtimestamp(end_ms / 1000, tz=UTC).date(),
        )
        results = await run_full_backtest(
            rest_client, db, settings, universe_symbols, START_MS, end_ms
        )

        print("\n=== RESUMEN DE VEREDICTOS (Fase 2) ===\n")
        for r in results:
            v = r.verdict
            estado = (
                "EXPERIMENTAL (no participa en el veredicto)" if v.is_experimental
                else "EVIDENCIA INSUFICIENTE" if v.evidence_insufficient
                else "DESCARTADA" if v.discarded
                else "SOBREVIVE LOS CRITERIOS"
            )
            print(f"--- {r.strategy_name}: {estado} ---")
            print(f"  combos probados: {v.combos_tested}")
            print(f"  trades totales: {v.total_trades_all_segments}")
            print(f"  PF OOS: {v.pf_oos_aggregate}  PF estresado: {v.pf_stressed}")
            print(f"  PF funding real: {v.pf_real_funding_only}")
            print(f"  PF control BTC/ETH: {v.pf_control_group}")
            print(f"  % simbolos PF>1: {v.pct_symbols_pf_gt1}")
            print(f"  % folds positivos: {v.pct_folds_positive}")
            print(f"  drawdown OOS: {v.max_drawdown_oos_pct}")
            print(f"  concentracion: {v.concentration_pct}")
            if v.discard_reasons_json:
                print(f"  motivos de descarte: {v.discard_reasons_json}")
            print()

        print(f"Tiempo total: {time.time() - t0:.1f}s")
    finally:
        await rest_client.aclose()
        await coingecko_client.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
