"""Ejecuta el backtest completo de Fase 2 y persiste todo en la base de
datos (`backtest_trades`, `backtest_runs`, `backtest_verdicts`).

Correccion post-Fase-2 (ver docs/FASE2_BLOQUEO_RED.md): este script ya NO
toca la red -- lee universo, specs, velas y funding exclusivamente de
SQLite. Si falta algo, lanza un error claro en vez de descargar nada. Corre
primero `scripts/download_history.py` (una sola vez; es idempotente y
reanudable) para llenar la base de datos:

    python scripts/download_history.py
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
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import universe_repo  # noqa: E402

logger = get_logger("run_backtest")

# Ancla de inicio anterior al piso real verificado de Bitunix (~2022-04-17):
# la paginacion hacia atras se detiene sola al agotar el historial.
START_MS = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)


async def main() -> None:
    setup_logging()
    t0 = time.time()
    db = Database(settings.database_path)
    await db.connect()

    try:
        universe_symbols = await universe_repo.get_included_symbols(db)
        if not universe_symbols:
            raise RuntimeError(
                "No hay universo cacheado en asset_universe. "
                "Corre primero: python scripts/download_history.py"
            )
        logger.info("Universo (desde cache): %s", universe_symbols)

        end_ms = int(time.time() * 1000)
        logger.info(
            "Corriendo backtest completo (%s a %s)...",
            datetime.fromtimestamp(START_MS / 1000, tz=UTC).date(),
            datetime.fromtimestamp(end_ms / 1000, tz=UTC).date(),
        )
        results = await run_full_backtest(db, settings, universe_symbols, START_MS, end_ms)

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
            if v.is_experimental and v.total_trades_all_segments == 0:
                print(
                    "  NOTA: 0 senales generadas -- revisa el umbral y la "
                    "ventana de historia que necesita la estrategia (ver "
                    "docs/FASE2_CRITERIOS.md)"
                )
            print(f"  PF OOS: {v.pf_oos_aggregate}  PF estresado: {v.pf_stressed}")
            print(f"  PF funding real: {v.pf_real_funding_only}")
            print(f"  PF control BTC/ETH: {v.pf_control_group}")
            print(f"  % simbolos PF>1: {v.pct_symbols_pf_gt1}")
            print(f"  % folds positivos: {v.pct_folds_positive}")
            print(f"  drawdown OOS: {v.max_drawdown_oos_pct}")
            print(
                f"  [informativo] cartera unica: drawdown {v.portfolio_max_drawdown_pct}%, "
                f"concentracion {v.portfolio_concentration_pct}, "
                f"capital final {v.portfolio_final_capital_usdt} USDT, "
                f"trades incluidos {v.portfolio_trades_included}, "
                f"omitidos por falta de margen {v.portfolio_trades_skipped_no_margin}"
            )
            print(f"  concentracion: {v.concentration_pct}")
            if v.discard_reasons_json:
                print(f"  motivos de descarte: {v.discard_reasons_json}")
            print()

        print(f"Tiempo total: {time.time() - t0:.1f}s")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
