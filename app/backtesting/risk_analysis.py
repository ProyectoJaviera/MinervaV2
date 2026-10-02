"""Analisis de riesgo de ruina con bootstrap sobre las operaciones reales
del backtest (tarea 5, cuarta revision post-Fase-2; corregido en la quinta
revision tras la revision de diseno del usuario antes de ejecutar el
script real -- ver `docs/FASE2_CRITERIOS.md`).

**Metodo**: se toma el conjunto de operaciones OOS de una estrategia (o de
varias combinadas, ver mas abajo) como la distribucion EMPIRICA de
resultados posibles por operacion (PnL neto como fraccion del margen
original, p.ej. -0.35 = perdida del 35% del margen). Para cada combinacion
de (margen por operacion, maximo de posiciones simultaneas, tope de
perdida del SL sobre el margen), se corren muchos ensayos bootstrap: en
cada ensayo se remuestrean (CON reemplazo) `trial_length` resultados de esa
distribucion, agrupados en lotes de tamano `max_simultaneous_positions`
(cada lote representa esa cantidad de posiciones abiertas "a la vez",
simplificacion documentada mas abajo), y se simula la cuenta (capital
inicial fijo) lote por lote.

**Simplificaciones documentadas** (no hay datos de trayectoria de precio
intra-operacion mas alla de lo que el backtest ya registro, asi que esto
es deliberadamente una aproximacion barata, no una re-simulacion completa):

1. **Tope de SL = EXCLUSION, no truncamiento** (corregido en esta revision):
   la version anterior truncaba la perdida de cada operacion remuestreada
   al tope de la grilla, lo que hacia que "un tope mas ajustado nunca
   aumenta la ruina" fuera cierto POR CONSTRUCCION (nunca se verificaba
   nada real -- truncar siempre reduce o mantiene igual la perdida). Ahora
   el tope de SL EXCLUYE del pool, antes de remuestrear, cualquier
   operacion cuyo riesgo planeado al abrir (`sl_margin_loss_pct`, fijado
   por el motor de backtest al momento de la entrada, independiente de
   como se cerro realmente) supere el tope -- exactamente como lo haria el
   bot en vivo, que simplemente nunca tomaria esa entrada (no se le
   ocurriria "tomarla pero cerrar antes"). Una operacion se simula ENTERA
   (con su resultado real) o se excluye entera: no hay truncamiento. Se
   reporta cuantas operaciones se excluyen por cada tope
   (`RiskScenario.excluded_by_sl_cap`). Esto significa que ya NO hay una
   garantia matematica de que un tope mas ajustado reduzca la ruina: si la
   operacion excluida resulta ser una ganadora grande (su SL planeado era
   amplio pero nunca se activo), excluirla puede EMPEORAR la esperanza del
   pool sobreviviente. Ver los tests que reemplazan a los anteriores
   (tautologicos) por este comportamiento real.
2. **Concurrencia por lotes**: en vez de modelar la superposicion temporal
   real de posiciones abiertas, se agrupan `max_simultaneous_positions`
   resultados remuestreados por lote, y el capital se actualiza con la
   SUMA del lote de una sola vez. Esto captura la direccion correcta del
   efecto (mas posiciones simultaneas -> mas varianza por ronda -> mas
   riesgo) sin necesitar reconstruir cuando exactamente se solapan en el
   tiempo las operaciones remuestreadas (que, al venir de un bootstrap, no
   tienen una linea de tiempo real propia).
3. **Reescalado por margen**: el PnL de cada operacion remuestreada se
   reescala como `pct_return * margin_usdt` de la grilla -- asume que
   comisiones/slippage escalan proporcionalmente con el tamano de la
   posicion (razonable a apalancamiento fijo, ya que las comisiones son un
   % del notional).
4. **Independencia i.i.d. (limitacion, no corregida de raiz)**: por
   defecto (`block_size=1`) cada remuestreo toma UNA operacion a la vez,
   tratandola como independiente de las demas. En la realidad, las
   perdidas se agrupan en el tiempo (un mismo regimen de mercado adverso
   golpea a varias operaciones seguidas de la misma estrategia), algo que
   el bootstrap i.i.d. **subestima sistematicamente** -- diluye las rachas
   reales en el remuestreo aleatorio. Como variante mas realista (a costo
   de menos combinaciones de muestreo posibles cuanto mayor el bloque),
   `block_size > 1` remuestrea BLOQUES de operaciones CONSECUTIVAS (en el
   orden cronologico original en que ocurrieron) en vez de operaciones
   sueltas, preservando parcialmente esa correlacion temporal dentro de
   cada bloque. Los reportes incluyen una comparacion i.i.d. vs. por
   bloques en el punto de la grilla mas relevante para decidir, no en toda
   la grilla (mas caro, poco informativo adicional).

**Ruina** = el capital de la cuenta cae por debajo del margen de esa
grilla (ya no puede abrir ni una posicion mas). Se reporta tambien la
probabilidad de un drawdown mayor al 30% en algun punto del ensayo.

**Por estrategia, no solo combinado**: esta revision corre la grilla
completa POR ESTRATEGIA (no un solo pool mezclado) -- mezclar estrategias
con perfiles de riesgo muy distintos (p.ej. `mean_reversion`, el 50%+ de
las operaciones y el peor PF, con `ema_cross`, la unica con PF cerca del
umbral) en un solo pool oculta de cual estrategia viene realmente el
riesgo. El pool combinado de las 4 no experimentales se conserva SOLO como
referencia, etiquetado explicitamente como "escenario con esperanza
negativa" (ver `scripts/analyze_risk.py`), y es tambien la base de los
escenarios de sensibilidad (deriva neutra / PF 1.2) que separan el efecto
de la deriva de la estrategia del efecto puramente estructural de
margen/posiciones simultaneas.

**Grilla de topes de SL (sexta revision -- corregido tras un fallo real al
ejecutar `scripts/analyze_risk.py` contra la base real)**: `ema_cross_9_21`
y las 2 estrategias de funding no definen un `stop_price` propio -- su SL
es el de respaldo porcentual (`BACKTEST_FALLBACK_SL_PCT=5%` de movimiento
de precio, que a 10x de apalancamiento es EXACTAMENTE 50% de perdida de
margen) en el 100% de sus operaciones. La grilla anterior incluia un tope
de 30%, que las deja con 0 operaciones elegibles -- `pct_returns` vacio,
lo que antes lanzaba `ValueError` y tumbaba el script entero a mitad de
camino. Dos correcciones, separadas:

- **Un escenario con 0 operaciones elegibles ya NO lanza excepcion**: se
  reporta como una fila valida con `prob_ruin_pct`/`prob_drawdown_gt_30_pct`
  en `None` (la tabla la muestra como "n/a") y `included_trades=0` --
  informativo, no un error. El resto de la grilla sigue calculandose
  normalmente.
- **La grilla de topes default cambia a `(0.40, 0.50, float("inf"))`**
  ("sin tope" representado como infinito, nunca excluye nada): a 40%
  seguiran apareciendo filas "n/a" para esas 3 estrategias (mismo motivo,
  informativo, no un bug); a 50% y "sin tope", el POOL incluido es
  IDENTICO para todas las estrategias de este dataset (mismas operaciones,
  0 excluidas en ambos casos), porque el backtest ya solo admitio
  operaciones con `sl_margin_loss_pct <= 50` (`MAX_SL_MARGIN_LOSS_PCT`) al
  generarlas -- un tope en vivo de 50% o mas permisivo no excluye nada que
  el backtest no haya excluido ya. Las PROBABILIDADES de esas dos filas
  pueden diferir levemente entre si de todos modos (`run_full_grid` usa un
  solo `random.Random` que avanza secuencialmente por toda la grilla, asi
  que cada fila consume un tramo distinto del muestreo aunque el pool de
  entrada sea igual) -- esa diferencia es ruido de Monte Carlo, no un
  error de calculo; lo que confirma la coincidencia es la columna
  "Incluidas"/"Excluidas por tope", no que las probabilidades deban salir
  bit a bit iguales.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Protocol

RUIN_DRAWDOWN_THRESHOLD_PCT = 30.0

DEFAULT_MARGINS_USDT = (5.0, 10.0)
DEFAULT_POSITION_CAPS = (1, 2, 3)
# 0.40 y 0.50 exploran el limite real (ver docstring del modulo); inf =
# "sin tope" (nunca excluye nada), de referencia -- a este dataset incluye
# el MISMO pool que 0.50 (0 exclusiones en ambos casos) porque el backtest
# ya no genero ninguna operacion con mas de 50% de riesgo planeado; las
# probabilidades de cada fila pueden diferir por ruido de muestreo.
DEFAULT_SL_CAPS_PCT = (0.40, 0.50, float("inf"))
DEFAULT_TRIALS = 2000
DEFAULT_TRIAL_LENGTH = 100
DEFAULT_BLOCK_SIZE_FOR_COMPARISON = 5
DEFAULT_SENSITIVITY_TARGET_PFS = (1.0, 1.2)


class TradeLike(Protocol):
    """Lo minimo que necesita este modulo de una operacion -- cumplido
    tanto por `app.backtesting.engine.RawTrade` (usado en tests, sin DB)
    como por `app.persistence.models.BacktestTrade` (lo que devuelve
    `backtest_repo.get_trades`, ya ordenado por `entry_time` ascendente)."""

    margin_usdt: float
    pnl_net_usdt: float
    sl_margin_loss_pct: float | None


@dataclass
class RiskScenario:
    margin_usdt: float
    max_simultaneous_positions: int
    sl_cap_pct: float
    # `None` cuando 0 operaciones sobreviven el tope de SL de esta fila --
    # escenario informativo ("n/a" en la tabla), no un error.
    prob_ruin_pct: float | None
    prob_drawdown_gt_30_pct: float | None
    trials: int
    trial_length: int
    excluded_by_sl_cap: int
    included_trades: int
    block_size: int = 1


@dataclass
class SensitivityScenario:
    target_pf: float
    margin_usdt: float
    max_simultaneous_positions: int
    prob_ruin_pct: float
    prob_drawdown_gt_30_pct: float
    trials: int
    trial_length: int


def pct_returns_from_trades(trades: list[TradeLike]) -> list[float]:
    """PnL neto de cada operacion como fraccion de SU margen original.
    Preserva el orden de `trades` (importante para el bootstrap por
    bloques, que asume orden cronologico)."""
    return [t.pnl_net_usdt / t.margin_usdt for t in trades if t.margin_usdt]


def filter_trades_by_sl_cap(
    trades: list[TradeLike], sl_cap_pct: float
) -> tuple[list[TradeLike], int]:
    """Excluye ENTERAS (sin truncar) las operaciones cuyo riesgo planeado al
    abrir (`sl_margin_loss_pct`, en unidades de porcentaje, p.ej. 35.0 =
    35%) supera `sl_cap_pct` (fraccion, p.ej. 0.30 = 30%) -- el bot en vivo
    nunca las habria tomado. Devuelve `(operaciones_incluidas,
    cantidad_excluida)`, preservando el orden original."""
    threshold_pct = sl_cap_pct * 100
    kept: list[TradeLike] = []
    excluded = 0
    for t in trades:
        if t.sl_margin_loss_pct is not None and t.sl_margin_loss_pct > threshold_pct:
            excluded += 1
        else:
            kept.append(t)
    return kept, excluded


def rescale_returns_to_target_pf(pct_returns: list[float], target_pf: float) -> list[float]:
    """Reescala SOLO las perdidas (multiplicador constante) para que el pool
    alcance exactamente `target_pf`, preservando la distribucion de
    ganancias y la proporcion relativa entre perdidas -- aisla el efecto de
    la deriva (profit factor) del efecto de margen/posiciones simultaneas
    en los escenarios de sensibilidad, sin inventar una distribucion
    sintetica nueva."""
    gains = sum(r for r in pct_returns if r > 0)
    losses_abs = sum(-r for r in pct_returns if r < 0)
    if losses_abs == 0:
        raise ValueError("El pool no tiene perdidas -- no se puede reescalar a un PF objetivo")
    scale = gains / (target_pf * losses_abs)
    return [r if r > 0 else r * scale for r in pct_returns]


def _draw_trial_returns(
    pct_returns: list[float], trial_length: int, block_size: int, rng: random.Random
) -> list[float]:
    if block_size <= 1:
        return [rng.choice(pct_returns) for _ in range(trial_length)]
    n = len(pct_returns)
    draws: list[float] = []
    while len(draws) < trial_length:
        start = rng.randrange(n)
        draws.extend(pct_returns[(start + i) % n] for i in range(block_size))
    return draws[:trial_length]


def _run_bootstrap_core(
    pct_returns: list[float],
    initial_capital: float,
    margin_usdt: float,
    max_simultaneous_positions: int,
    rng: random.Random,
    trials: int,
    trial_length: int,
    block_size: int,
) -> tuple[float, float]:
    """Nucleo mecanico del bootstrap (remuestreo + lotes + ruina/drawdown),
    sin ninguna nocion de tope de SL -- la usan tanto `run_bootstrap_scenario`
    (que ya filtro el pool por tope de SL antes de llamarla) como los
    escenarios de sensibilidad (retornos sinteticos reescalados, sin
    operaciones ni tope de SL de por medio). Devuelve
    `(prob_ruina_pct, prob_drawdown_gt_30_pct)`."""
    if not pct_returns:
        raise ValueError("pct_returns no puede estar vacio")
    if max_simultaneous_positions < 1:
        raise ValueError("max_simultaneous_positions debe ser >= 1")

    ruin_count = 0
    drawdown_count = 0

    for _ in range(trials):
        trial_returns = _draw_trial_returns(pct_returns, trial_length, block_size, rng)
        capital = initial_capital
        peak = initial_capital
        max_dd = 0.0
        ruined = False
        idx = 0
        while idx < len(trial_returns):
            batch = trial_returns[idx : idx + max_simultaneous_positions]
            batch_pnl = sum(r * margin_usdt for r in batch)
            capital = max(0.0, capital + batch_pnl)
            peak = max(peak, capital)
            if peak > 0:
                max_dd = max(max_dd, (peak - capital) / peak * 100)
            idx += len(batch)
            if capital < margin_usdt:
                ruined = True
                break
        if ruined:
            ruin_count += 1
        if max_dd > RUIN_DRAWDOWN_THRESHOLD_PCT:
            drawdown_count += 1

    return ruin_count / trials * 100, drawdown_count / trials * 100


def run_bootstrap_scenario(
    trades: list[TradeLike],
    initial_capital: float,
    margin_usdt: float,
    max_simultaneous_positions: int,
    sl_cap_pct: float,
    rng: random.Random,
    trials: int = DEFAULT_TRIALS,
    trial_length: int = DEFAULT_TRIAL_LENGTH,
    block_size: int = 1,
) -> RiskScenario:
    kept, excluded = filter_trades_by_sl_cap(trades, sl_cap_pct)
    pct_returns = pct_returns_from_trades(kept)

    if not pct_returns:
        # 0 operaciones elegibles bajo este tope (p.ej. una estrategia cuyo
        # unico SL es un porcentaje fijo mas amplio que el tope de esta
        # fila): informativo, no un error -- ver docstring del modulo.
        return RiskScenario(
            margin_usdt=margin_usdt,
            max_simultaneous_positions=max_simultaneous_positions,
            sl_cap_pct=sl_cap_pct,
            prob_ruin_pct=None,
            prob_drawdown_gt_30_pct=None,
            trials=trials,
            trial_length=trial_length,
            excluded_by_sl_cap=excluded,
            included_trades=0,
            block_size=block_size,
        )

    prob_ruin, prob_dd = _run_bootstrap_core(
        pct_returns, initial_capital, margin_usdt, max_simultaneous_positions,
        rng, trials, trial_length, block_size,
    )
    return RiskScenario(
        margin_usdt=margin_usdt,
        max_simultaneous_positions=max_simultaneous_positions,
        sl_cap_pct=sl_cap_pct,
        prob_ruin_pct=prob_ruin,
        prob_drawdown_gt_30_pct=prob_dd,
        trials=trials,
        trial_length=trial_length,
        excluded_by_sl_cap=excluded,
        included_trades=len(pct_returns),
        block_size=block_size,
    )


def run_full_grid(
    trades: list[TradeLike],
    initial_capital: float = 100.0,
    margins: tuple[float, ...] = DEFAULT_MARGINS_USDT,
    position_caps: tuple[int, ...] = DEFAULT_POSITION_CAPS,
    sl_caps: tuple[float, ...] = DEFAULT_SL_CAPS_PCT,
    trials: int = DEFAULT_TRIALS,
    trial_length: int = DEFAULT_TRIAL_LENGTH,
    seed: int = 42,
    block_size: int = 1,
) -> list[RiskScenario]:
    """Corre `run_bootstrap_scenario` para toda la grilla
    margen x posiciones x tope-SL, con UN solo `random.Random(seed)`
    compartido (avanza secuencialmente por toda la grilla en un orden
    fijo) -- reproducible de punta a punta con un solo seed."""
    rng = random.Random(seed)
    return [
        run_bootstrap_scenario(
            trades, initial_capital, margin, positions, sl_cap, rng,
            trials=trials, trial_length=trial_length, block_size=block_size,
        )
        for margin in margins
        for positions in position_caps
        for sl_cap in sl_caps
    ]


def run_sensitivity_grid(
    pct_returns: list[float],
    initial_capital: float = 100.0,
    margins: tuple[float, ...] = DEFAULT_MARGINS_USDT,
    position_caps: tuple[int, ...] = DEFAULT_POSITION_CAPS,
    target_pfs: tuple[float, ...] = DEFAULT_SENSITIVITY_TARGET_PFS,
    trials: int = DEFAULT_TRIALS,
    trial_length: int = DEFAULT_TRIAL_LENGTH,
    seed: int = 42,
) -> list[SensitivityScenario]:
    """Reescala `pct_returns` a cada `target_pf` (ver
    `rescale_returns_to_target_pf`) y corre la grilla margen x posiciones
    (sin tope de SL -- ya aplicado, si corresponde, por el llamador antes
    de pasar `pct_returns`) -- aisla el efecto de la deriva del efecto
    estructural de margen/posiciones simultaneas."""
    rng = random.Random(seed)
    results = []
    for target_pf in target_pfs:
        rescaled = rescale_returns_to_target_pf(pct_returns, target_pf)
        for margin in margins:
            for positions in position_caps:
                prob_ruin, prob_dd = _run_bootstrap_core(
                    rescaled, initial_capital, margin, positions, rng,
                    trials, trial_length, block_size=1,
                )
                results.append(SensitivityScenario(
                    target_pf=target_pf, margin_usdt=margin,
                    max_simultaneous_positions=positions,
                    prob_ruin_pct=prob_ruin, prob_drawdown_gt_30_pct=prob_dd,
                    trials=trials, trial_length=trial_length,
                ))
    return results


def format_markdown_table(results: list[RiskScenario]) -> str:
    lines = [
        "| Margen (USDT) | Máx. posiciones simultáneas | Tope SL (% margen) "
        "| Bloque | Excluidas por tope | Incluidas | P(ruina) | P(drawdown > 30%) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        sl_cap_str = "sin tope" if math.isinf(r.sl_cap_pct) else f"{r.sl_cap_pct * 100:.0f}%"
        if r.prob_ruin_pct is None:
            total = r.excluded_by_sl_cap + r.included_trades
            ruin_str = f"n/a (0 de {total} elegibles)"
            dd_str = "n/a"
        else:
            ruin_str = f"{r.prob_ruin_pct:.1f}%"
            dd_str = f"{r.prob_drawdown_gt_30_pct:.1f}%"
        lines.append(
            f"| {r.margin_usdt:.0f} | {r.max_simultaneous_positions} | "
            f"{sl_cap_str} | {r.block_size} | {r.excluded_by_sl_cap} | "
            f"{r.included_trades} | {ruin_str} | {dd_str} |"
        )
    return "\n".join(lines)


def format_sensitivity_table(results: list[SensitivityScenario]) -> str:
    lines = [
        "| PF objetivo | Margen (USDT) | Máx. posiciones simultáneas "
        "| P(ruina) | P(drawdown > 30%) |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.target_pf:.1f} | {r.margin_usdt:.0f} | "
            f"{r.max_simultaneous_positions} | {r.prob_ruin_pct:.1f}% | "
            f"{r.prob_drawdown_gt_30_pct:.1f}% |"
        )
    return "\n".join(lines)
