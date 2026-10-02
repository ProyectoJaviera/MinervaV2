"""Analisis de riesgo de ruina con bootstrap sobre las operaciones OOS del
backtest real (tarea 5, quinta revision: por estrategia, exclusion -- no
truncamiento -- por tope de SL, escenarios de sensibilidad y comparacion
de bootstrap por bloques) -- escribe `docs/FASE2_RIESGO.md`.

Lee SOLO de SQLite (no toca la red); corre despues de
`scripts/run_backtest.py`. Ver `app/backtesting/risk_analysis.py` para el
metodo completo y las simplificaciones documentadas.

Uso:
    python scripts/analyze_risk.py
"""

from __future__ import annotations

import asyncio
import random
import sys
import time

sys.path.insert(0, ".")

from app.backtesting.metrics import compute_metrics  # noqa: E402
from app.backtesting.risk_analysis import (  # noqa: E402
    DEFAULT_BLOCK_SIZE_FOR_COMPARISON,
    DEFAULT_TRIAL_LENGTH,
    DEFAULT_TRIALS,
    filter_trades_by_sl_cap,
    format_markdown_table,
    format_sensitivity_table,
    pct_returns_from_trades,
    run_bootstrap_scenario,
    run_full_grid,
    run_sensitivity_grid,
)
from app.config import settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import backtest_repo  # noqa: E402
from app.strategies.registry import EXPERIMENTAL_STRATEGIES, STRATEGIES  # noqa: E402

logger = get_logger("analyze_risk")

REPORT_PATH = "docs/FASE2_RIESGO.md"

# Punto de la grilla usado para la comparacion i.i.d. vs. bootstrap por
# bloques y para los escenarios de sensibilidad (un solo punto
# representativo, no toda la grilla -- mas barato, igual de informativo
# para mostrar la direccion del efecto): el margen/tope de SL que
# docs/FASE3_PLAN.md propone hoy como provisional. El tope es 50%, no 30%
# -- a 30%, ema_cross_9_21 y las 2 estrategias de funding quedan con 0
# operaciones incluidas (su unico SL es el de respaldo, fijo en exactamente
# 50% de perdida de margen) -- ver seccion 4 de docs/FASE3_PLAN.md.
BLOCK_COMPARISON_MARGIN_USDT = 5.0
BLOCK_COMPARISON_POSITIONS = 3
BLOCK_COMPARISON_SL_CAP_PCT = 0.50

# Nombres propios del script (no los defaults de risk_analysis.py
# directamente) para que un test pueda bajarlos con `monkeypatch` sin
# tocar el comportamiento real -- los defaults de parametro de las
# funciones de risk_analysis.py quedan fijados en tiempo de definicion,
# monkeypatchear sus constantes de modulo despues no los cambiaria.
GRID_TRIALS = DEFAULT_TRIALS
GRID_TRIAL_LENGTH = DEFAULT_TRIAL_LENGTH

# Estrategias no experimentales -- el pool combinado (solo de referencia,
# ver docstring de risk_analysis.py) se construye con estas 4.
NON_EXPERIMENTAL_STRATEGIES = tuple(s for s in STRATEGIES if s not in EXPERIMENTAL_STRATEGIES)
ALL_STRATEGIES = NON_EXPERIMENTAL_STRATEGIES + tuple(EXPERIMENTAL_STRATEGIES)


def _strategy_summary_line(strategy: str, trades: list) -> str:
    m = compute_metrics(trades, settings.backtest_initial_capital)
    pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "N/A"
    expectancy = f"{m.expectancy_usdt:+.2f} USDT" if m.expectancy_usdt is not None else "N/A"
    tag = " (experimental)" if strategy in EXPERIMENTAL_STRATEGIES else ""
    return (
        f"**{strategy}{tag}** -- {m.total_trades} operaciones OOS, "
        f"PF {pf}, esperanza por operación {expectancy}."
    )


def _strategy_section(strategy: str, trades: list) -> str:
    if not trades:
        return (
            f"### `{strategy}`\n\nSin operaciones OOS -- no se puede correr "
            "el análisis de riesgo para esta estrategia.\n"
        )

    grid = run_full_grid(
        trades, initial_capital=settings.backtest_initial_capital,
        trials=GRID_TRIALS, trial_length=GRID_TRIAL_LENGTH,
    )
    grid_table = format_markdown_table(grid)

    iid_scenario = run_bootstrap_scenario(
        trades, settings.backtest_initial_capital, BLOCK_COMPARISON_MARGIN_USDT,
        BLOCK_COMPARISON_POSITIONS, BLOCK_COMPARISON_SL_CAP_PCT,
        rng=random.Random(123), trials=GRID_TRIALS, trial_length=GRID_TRIAL_LENGTH,
        block_size=1,
    )
    block_scenario = run_bootstrap_scenario(
        trades, settings.backtest_initial_capital, BLOCK_COMPARISON_MARGIN_USDT,
        BLOCK_COMPARISON_POSITIONS, BLOCK_COMPARISON_SL_CAP_PCT,
        rng=random.Random(123), trials=GRID_TRIALS, trial_length=GRID_TRIAL_LENGTH,
        block_size=DEFAULT_BLOCK_SIZE_FOR_COMPARISON,
    )
    block_table = format_markdown_table([iid_scenario, block_scenario])

    return (
        f"### `{strategy}`\n\n"
        f"{_strategy_summary_line(strategy, trades)}\n\n"
        f"{grid_table}\n\n"
        "**i.i.d. vs. bootstrap por bloques** (mismo punto de la grilla: "
        f"margen {BLOCK_COMPARISON_MARGIN_USDT:.0f} USDT, "
        f"{BLOCK_COMPARISON_POSITIONS} posiciones, tope SL "
        f"{BLOCK_COMPARISON_SL_CAP_PCT * 100:.0f}% -- bloque de "
        f"{DEFAULT_BLOCK_SIZE_FOR_COMPARISON} operaciones consecutivas "
        "vs. i.i.d.; ver limitación 4 del docstring de "
        "`app/backtesting/risk_analysis.py`):\n\n"
        f"{block_table}\n"
    )


async def main() -> None:
    setup_logging()
    t0 = time.time()
    db = Database(settings.database_path)
    await db.connect()

    try:
        trades_by_strategy: dict[str, list] = {}
        for strategy in ALL_STRATEGIES:
            trades = await backtest_repo.get_trades(db, strategy, segment="OOS")
            logger.info("%s: %d operaciones OOS", strategy, len(trades))
            trades_by_strategy[strategy] = trades

        sections = [
            _strategy_section(strategy, trades_by_strategy[strategy])
            for strategy in ALL_STRATEGIES
        ]

        combined_trades = [
            t for s in NON_EXPERIMENTAL_STRATEGIES for t in trades_by_strategy[s]
        ]
        if not combined_trades:
            raise RuntimeError(
                "No hay operaciones OOS en backtest_trades. "
                "Corre primero: python scripts/download_history.py && "
                "python scripts/run_backtest.py"
            )
        combined_summary = _strategy_summary_line(
            "pool combinado (" + ", ".join(NON_EXPERIMENTAL_STRATEGIES) + ")", combined_trades,
        )
        combined_grid = run_full_grid(
            combined_trades, initial_capital=settings.backtest_initial_capital,
            trials=GRID_TRIALS, trial_length=GRID_TRIAL_LENGTH,
        )
        combined_table = format_markdown_table(combined_grid)

        kept_for_sensitivity, excluded_for_sensitivity = filter_trades_by_sl_cap(
            combined_trades, BLOCK_COMPARISON_SL_CAP_PCT,
        )
        base_returns = pct_returns_from_trades(kept_for_sensitivity)
        sensitivity_results = run_sensitivity_grid(
            base_returns, initial_capital=settings.backtest_initial_capital,
            trials=GRID_TRIALS, trial_length=GRID_TRIAL_LENGTH,
        )
        sensitivity_table = format_sensitivity_table(sensitivity_results)

        strategies_section = "\n".join(sections)
        report = f"""# Fase 2 — Análisis de riesgo de ruina (bootstrap)

> Generado por `scripts/analyze_risk.py`. Ver `app/backtesting/risk_analysis.py`
> para el método completo y las simplificaciones documentadas (exclusión —no
> truncamiento— por tope de SL, concurrencia por lotes, reescalado
> proporcional por margen, bootstrap i.i.d. por defecto con una variante por
> bloques). Capital inicial: {settings.backtest_initial_capital:.0f} USDT.
> Esta tabla es la base empírica para los parámetros de gestión de riesgo
> propuestos en `docs/FASE3_PLAN.md` — no sustituye los criterios de
> descarte congelados de `docs/FASE2_CRITERIOS.md` (esos evalúan si una
> estrategia vale la pena correr; esto evalúa qué tan agresivos pueden ser
> los parámetros de riesgo de cualquier estrategia con un perfil de
> resultados parecido al observado).

## Por estrategia

{strategies_section}

## Pool combinado (solo referencia — escenario con esperanza negativa)

{combined_summary}

Combina las {len(NON_EXPERIMENTAL_STRATEGIES)} estrategias no
experimentales en una sola distribución empírica — **no** representa a
ninguna estrategia real por sí sola, es una referencia de "qué tan mal
podría ir" si el mercado se parece al conjunto observado. Las tablas por
estrategia de arriba son las que informan decisiones específicas por
estrategia.

{combined_table}

## Sensibilidad: aislando la deriva del efecto de margen/posiciones

Mismo pool combinado, filtrado primero al tope de SL del
{BLOCK_COMPARISON_SL_CAP_PCT * 100:.0f}% ({excluded_for_sensitivity}
operaciones excluidas de {len(combined_trades)}), con las pérdidas
reescaladas para alcanzar exactamente el profit factor objetivo de cada
fila (las ganancias no se tocan) — separa cuánto del riesgo de ruina viene
de la deriva negativa de las estrategias evaluadas de cuánto viene,
estructuralmente, del margen y las posiciones simultáneas elegidas, para
cualquier estrategia con un perfil de resultados parecido:

{sensitivity_table}

## Lectura

- **P(ruina)**: probabilidad de que el capital caiga por debajo del margen
  de esa fila (ya no se puede abrir ni una operación más) en algún punto de
  un ensayo de 100 operaciones.
- **P(drawdown > 30%)**: probabilidad de que el capital caiga más del 30%
  desde su máximo histórico en algún punto del mismo ensayo.
- **Excluidas por tope** / **Incluidas**: cuántas operaciones del pool de
  esa fila el tope de SL de esa fila excluye por completo (riesgo
  planeado al abrir mayor al tope) vs. cuántas sobreviven para remuestrear
  — el tope de SL ya NO trunca pérdidas, excluye operaciones enteras (ver
  `app/backtesting/risk_analysis.py`).
- **Bloque**: tamaño del bloque de operaciones consecutivas remuestreadas
  juntas (1 = i.i.d., el default; mayor a 1 = preserva algo de la
  correlación temporal real entre pérdidas consecutivas, que el i.i.d.
  subestima).
"""

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(report)

        print(f"Escrito en {REPORT_PATH}")
        print(f"Tiempo total: {time.time() - t0:.1f}s")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
