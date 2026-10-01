"""Analisis de riesgo de ruina con bootstrap sobre las operaciones OOS del
backtest real (tarea 5, cuarta revision post-Fase-2) -- escribe
`docs/FASE2_RIESGO.md`.

Lee SOLO de SQLite (no toca la red); corre despues de
`scripts/run_backtest.py`. Ver `app/backtesting/risk_analysis.py` para el
metodo completo y las simplificaciones documentadas.

Uso:
    python scripts/analyze_risk.py
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.backtesting.risk_analysis import (  # noqa: E402
    format_markdown_table,
    pct_returns_from_trades,
    run_full_grid,
)
from app.config import settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import backtest_repo  # noqa: E402

logger = get_logger("analyze_risk")

# Estrategias no experimentales -- la distribucion empirica se construye
# combinando sus operaciones OOS, no la de una sola (ver docstring del
# modulo app/backtesting/risk_analysis.py para la justificacion).
POOL_STRATEGIES = (
    "ema_cross_9_21",
    "trend_atr_stop_9_21_50",
    "mean_reversion_rsi14_bb20",
    "donchian_breakout_20",
)

REPORT_PATH = "docs/FASE2_RIESGO.md"


async def main() -> None:
    setup_logging()
    t0 = time.time()
    db = Database(settings.database_path)
    await db.connect()

    try:
        all_trades = []
        for strategy in POOL_STRATEGIES:
            trades = await backtest_repo.get_trades(db, strategy, segment="OOS")
            logger.info("%s: %d operaciones OOS", strategy, len(trades))
            all_trades.extend(trades)

        if not all_trades:
            raise RuntimeError(
                "No hay operaciones OOS en backtest_trades. "
                "Corre primero: python scripts/download_history.py && "
                "python scripts/run_backtest.py"
            )

        pct_returns = pct_returns_from_trades(all_trades)
        logger.info(
            "Pool combinado: %d operaciones OOS de %d estrategias",
            len(pct_returns), len(POOL_STRATEGIES),
        )

        results = run_full_grid(pct_returns, initial_capital=settings.backtest_initial_capital)
        table = format_markdown_table(results)

        strategies_list = ", ".join(POOL_STRATEGIES)
        intro = (
            f"> Generado por `scripts/analyze_risk.py` sobre las {len(pct_returns)} "
            f"operaciones OOS combinadas de {len(POOL_STRATEGIES)} estrategias no "
            f"experimentales ({strategies_list}). Ver `app/backtesting/risk_analysis.py` "
            "para el método completo y las simplificaciones documentadas (tope de SL "
            "simulado por truncamiento, concurrencia por lotes, reescalado proporcional "
            f"por margen). Capital inicial: {settings.backtest_initial_capital:.0f} USDT. "
            f"{results[0].trials} ensayos bootstrap de {results[0].trial_length} "
            "operaciones cada uno, por combinación."
        )
        ruin_bullet = (
            "- **P(ruina)**: probabilidad de que el capital caiga por debajo del margen "
            "de esa fila (ya no se puede abrir ni una operación más) en algún punto de "
            f"un ensayo de {results[0].trial_length} operaciones."
        )
        drawdown_bullet = (
            "- **P(drawdown > 30%)**: probabilidad de que el capital caiga más del 30% "
            "desde su máximo histórico en algún punto del mismo ensayo."
        )
        context_bullet = (
            "- Esta tabla es la base empírica para los parámetros de gestión de riesgo "
            "propuestos en `docs/FASE3_PLAN.md` — no sustituye los criterios de descarte "
            "congelados de `docs/FASE2_CRITERIOS.md` (esos evalúan si una estrategia vale "
            "la pena correr; esto evalúa qué tan agresivos pueden ser los parámetros de "
            "riesgo de cualquier estrategia con un perfil de resultados parecido al "
            "observado)."
        )
        report = (
            "# Fase 2 — Análisis de riesgo de ruina (bootstrap)\n\n"
            f"{intro}\n\n"
            "## Tabla de riesgo\n\n"
            f"{table}\n\n"
            "## Lectura\n\n"
            f"{ruin_bullet}\n"
            f"{drawdown_bullet}\n"
            f"{context_bullet}\n"
        )

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(report)

        print(table)
        print(f"\nEscrito en {REPORT_PATH}")
        print(f"Tiempo total: {time.time() - t0:.1f}s")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
