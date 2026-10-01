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
from app.market.ohlcv_history import check_series_availability  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import universe_repo  # noqa: E402
from app.strategies.registry import STRATEGY_TIMEFRAMES  # noqa: E402

logger = get_logger("run_backtest")

# Ancla de inicio anterior al piso real verificado de Bitunix (~2022-04-17):
# la paginacion hacia atras se detiene sola al agotar el historial.
START_MS = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)


async def _find_missing_series(
    db: Database, universe_symbols: list[str], start_ms: int, end_ms: int
) -> list[str]:
    """Verifica TODAS las series (simbolo x timeframe x tipo de precio)
    que alguna estrategia va a necesitar, ANTES de calcular nada -- evita
    descubrir a mitad de una corrida (tras minutos de computo ya hecho)
    que falta una sola serie (tarea 1a, correccion post-Fase-2: una
    corrida real fallo asi en BNBUSDT 1h)."""
    all_symbols = sorted(set(universe_symbols) | set(settings.backtest_control_symbols_list))
    timeframes = sorted({tf for tfs in STRATEGY_TIMEFRAMES.values() for tf in tfs})

    problems: list[str] = []
    for symbol in all_symbols:
        for tf in timeframes:
            for price_type in ("LAST_PRICE", "MARK_PRICE"):
                problem = await check_series_availability(
                    db, symbol, tf, start_ms, end_ms, price_type
                )
                if problem is not None:
                    problems.append(problem)
    return problems


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

        # Fecha final FIJA para que el veredicto oficial sea reproducible
        # (dos corridas dan exactamente los mismos numeros) -- ver
        # docs/FASE2_CRITERIOS.md para la fecha congelada y por que.
        end_ms = settings.backtest_official_end_ms
        logger.info(
            "Corriendo backtest completo (%s a %s)...",
            datetime.fromtimestamp(START_MS / 1000, tz=UTC).date(),
            datetime.fromtimestamp(end_ms / 1000, tz=UTC).date(),
        )

        missing = await _find_missing_series(db, universe_symbols, START_MS, end_ms)
        if missing:
            print(f"\nFaltan {len(missing)} serie(s) antes de poder correr el backtest:\n")
            for problem in missing:
                print(f"  - {problem}")
            print("\nCorre primero: python scripts/download_history.py")
            return

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
            print(
                f"  degradacion IS -> OOS: PF IS {v.pf_is_aggregate} "
                f"({v.is_trades_count} trades) -> PF OOS {v.pf_oos_aggregate} "
                f"({v.oos_trades_count} trades)"
            )
            print(f"  PF estresado: {v.pf_stressed}")
            print(f"  PF funding real: {v.pf_real_funding_only}")
            print(f"  PF control BTC/ETH: {v.pf_control_group}")
            print(f"  % simbolos PF>1: {v.pct_symbols_pf_gt1}")
            print(f"  % folds positivos: {v.pct_folds_positive}")
            print(f"  drawdown OOS: {v.max_drawdown_oos_pct}")
            print(
                f"  [informativo, {v.portfolio_simulation_runs} corridas Monte Carlo] "
                f"cartera unica -- capital final: mediana {v.portfolio_final_capital_median} "
                f"USDT (p10-p90: {v.portfolio_final_capital_p10}-{v.portfolio_final_capital_p90})"
            )
            print(
                f"    drawdown solo-al-cierre: mediana {v.portfolio_max_drawdown_median}% "
                f"(p10-p90: {v.portfolio_max_drawdown_p10}-{v.portfolio_max_drawdown_p90})"
            )
            print(
                f"    drawdown mark-to-market: mediana {v.portfolio_mtm_max_drawdown_median}% "
                f"(p10-p90: {v.portfolio_mtm_max_drawdown_p10}-{v.portfolio_mtm_max_drawdown_p90})"
            )
            print(
                f"    concentracion (mediana): {v.portfolio_concentration_pct_median}, "
                f"trades incluidos (mediana): {v.portfolio_trades_included_median}, "
                f"omitidos por falta de margen (mediana): "
                f"{v.portfolio_trades_skipped_no_margin_median}"
            )
            print(
                f"  [informativo, {v.portfolio_oos_simulation_runs} corridas Monte Carlo, "
                f"SOLO OOS] cartera unica -- capital final: mediana "
                f"{v.portfolio_oos_final_capital_median} USDT (p10-p90: "
                f"{v.portfolio_oos_final_capital_p10}-{v.portfolio_oos_final_capital_p90})"
            )
            print(
                f"    drawdown solo-al-cierre (OOS): mediana "
                f"{v.portfolio_oos_max_drawdown_median}% (p10-p90: "
                f"{v.portfolio_oos_max_drawdown_p10}-{v.portfolio_oos_max_drawdown_p90})"
            )
            print(
                f"    drawdown mark-to-market (OOS): mediana "
                f"{v.portfolio_oos_mtm_max_drawdown_median}% (p10-p90: "
                f"{v.portfolio_oos_mtm_max_drawdown_p10}-{v.portfolio_oos_mtm_max_drawdown_p90})"
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
