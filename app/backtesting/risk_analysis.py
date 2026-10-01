"""Analisis de riesgo de ruina con bootstrap sobre las operaciones reales
del backtest (tarea 5, cuarta revision post-Fase-2).

**Metodo**: se toma el conjunto de operaciones OOS de las estrategias no
experimentales como la distribucion EMPIRICA de resultados posibles por
operacion (PnL neto como fraccion del margen original, p.ej. -0.35 =
perdida del 35% del margen) -- no una sola estrategia, sino el conjunto
combinado, para representar el RANGO de resultados que este mercado
produjo con varias familias de estrategias razonables, no la fortuna de
una sola. Luego, para cada combinacion de (margen por operacion, maximo de
posiciones simultaneas, tope de perdida del SL sobre el margen), se corren
muchos ensayos bootstrap: en cada ensayo se remuestrean (CON reemplazo)
`trial_length` resultados de esa distribucion, agrupados en lotes de
tamano `max_simultaneous_positions` (cada lote representa esa cantidad de
posiciones abiertas "a la vez", simplificacion documentada mas abajo), y
se simula la cuenta (capital inicial fijo) lote por lote.

**Simplificaciones documentadas** (no hay datos de trayectoria de precio
intra-operacion mas alla de lo que el backtest ya registro, asi que esto
es deliberadamente una aproximacion barata, no una re-simulacion completa):

1. **Tope de SL simulado**: si una operacion remuestreada perderia mas del
   tope de esta grilla (30% o 50% del margen), se trunca exactamente en el
   tope -- se asume que un SL mas ajustado habria cerrado la posicion en
   ese punto de la MISMA trayectoria adversa que el backtest ya registro,
   no que la operacion se habria evitado del todo.
2. **Concurrencia por lotes**: en vez de modelar la superposicion temporal
   real de posiciones abiertas, se agrupan `max_simultaneous_positions`
   resultados remuestreados por lote, y el capital se actualiza con la
   SUMA del lote de una sola vez. Esto captura la direccion correcta del
   efecto (mas posiciones simultaneas -> mas varianza por ronda -> mas
   riesgo) sin necesitar reconstruir cuando exactamente se solapan en el
   tiempo las operaciones remuestreadas (que, al venir de un bootstrap,
   no tienen una linea de tiempo real propia).
3. **Reescalado por margen**: el PnL de cada operacion remuestreada se
   reescala como `pct_return * margin_usdt` de la grilla -- asume que
   comisiones/slippage escalan proporcionalmente con el tamano de la
   posicion (razonable a apalancamiento fijo, ya que las comisiones son un
   % del notional).

**Ruina** = el capital de la cuenta cae por debajo del margen de esa
grilla (ya no puede abrir ni una posicion mas). Se reporta tambien la
probabilidad de un drawdown mayor al 30% en algun punto del ensayo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.backtesting.engine import RawTrade

RUIN_DRAWDOWN_THRESHOLD_PCT = 30.0

DEFAULT_MARGINS_USDT = (5.0, 10.0)
DEFAULT_POSITION_CAPS = (1, 2, 3)
DEFAULT_SL_CAPS_PCT = (0.30, 0.50)
DEFAULT_TRIALS = 2000
DEFAULT_TRIAL_LENGTH = 100


@dataclass
class RiskScenario:
    margin_usdt: float
    max_simultaneous_positions: int
    sl_cap_pct: float
    prob_ruin_pct: float
    prob_drawdown_gt_30_pct: float
    trials: int
    trial_length: int


def pct_returns_from_trades(trades: list[RawTrade]) -> list[float]:
    """PnL neto de cada operacion como fraccion de SU margen original."""
    return [t.pnl_net_usdt / t.margin_usdt for t in trades if t.margin_usdt]


def _apply_sl_cap(pct_return: float, sl_cap_pct: float) -> float:
    if pct_return < -sl_cap_pct:
        return -sl_cap_pct
    return pct_return


def run_bootstrap_scenario(
    pct_returns: list[float],
    initial_capital: float,
    margin_usdt: float,
    max_simultaneous_positions: int,
    sl_cap_pct: float,
    rng: random.Random,
    trials: int = DEFAULT_TRIALS,
    trial_length: int = DEFAULT_TRIAL_LENGTH,
) -> RiskScenario:
    if not pct_returns:
        raise ValueError("pct_returns no puede estar vacio")
    if max_simultaneous_positions < 1:
        raise ValueError("max_simultaneous_positions debe ser >= 1")

    ruin_count = 0
    drawdown_count = 0

    for _ in range(trials):
        capital = initial_capital
        peak = initial_capital
        max_dd = 0.0
        ruined = False
        remaining = trial_length
        while remaining > 0:
            batch_size = min(max_simultaneous_positions, remaining)
            batch_pnl = sum(
                _apply_sl_cap(rng.choice(pct_returns), sl_cap_pct) * margin_usdt
                for _ in range(batch_size)
            )
            capital = max(0.0, capital + batch_pnl)
            peak = max(peak, capital)
            if peak > 0:
                max_dd = max(max_dd, (peak - capital) / peak * 100)
            remaining -= batch_size
            if capital < margin_usdt:
                ruined = True
                break
        if ruined:
            ruin_count += 1
        if max_dd > RUIN_DRAWDOWN_THRESHOLD_PCT:
            drawdown_count += 1

    return RiskScenario(
        margin_usdt=margin_usdt,
        max_simultaneous_positions=max_simultaneous_positions,
        sl_cap_pct=sl_cap_pct,
        prob_ruin_pct=ruin_count / trials * 100,
        prob_drawdown_gt_30_pct=drawdown_count / trials * 100,
        trials=trials,
        trial_length=trial_length,
    )


def run_full_grid(
    pct_returns: list[float],
    initial_capital: float = 100.0,
    margins: tuple[float, ...] = DEFAULT_MARGINS_USDT,
    position_caps: tuple[int, ...] = DEFAULT_POSITION_CAPS,
    sl_caps: tuple[float, ...] = DEFAULT_SL_CAPS_PCT,
    trials: int = DEFAULT_TRIALS,
    trial_length: int = DEFAULT_TRIAL_LENGTH,
    seed: int = 42,
) -> list[RiskScenario]:
    """Corre `run_bootstrap_scenario` para toda la grilla
    margen x posiciones x tope-SL, con UN solo `random.Random(seed)`
    compartido (avanza secuencialmente por toda la grilla en un orden
    fijo) -- reproducible de punta a punta con un solo seed."""
    rng = random.Random(seed)
    return [
        run_bootstrap_scenario(
            pct_returns, initial_capital, margin, positions, sl_cap, rng,
            trials=trials, trial_length=trial_length,
        )
        for margin in margins
        for positions in position_caps
        for sl_cap in sl_caps
    ]


def format_markdown_table(results: list[RiskScenario]) -> str:
    lines = [
        "| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) "
        "| P(ruina) | P(drawdown > 30%) |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.margin_usdt:.0f} | {r.max_simultaneous_positions} | "
            f"{r.sl_cap_pct * 100:.0f}% | {r.prob_ruin_pct:.1f}% | "
            f"{r.prob_drawdown_gt_30_pct:.1f}% |"
        )
    return "\n".join(lines)
