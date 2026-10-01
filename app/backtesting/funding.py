"""Construccion de la serie de funding alineada a las velas del backtest, y
calculo del costo de funding por vela.

Simplificacion documentada (ver docs/FASE2_PLAN.md seccion A): en vez de
aplicar el funding como eventos puntuales en los horarios reales de
settlement, se prorratea linealmente segun la duracion de la vela respecto
al `fundingInterval` del contrato. El efecto acumulado esperado es
equivalente; evita tener que reconciliar los limites de vela con los
horarios exactos de settlement.

Cuando no hay dato real de funding para una fecha (antes de ~2024 para la
mayoria de los simbolos, ver docs/FASE2_PLAN.md), se usa la MEDIANA del
funding real observado para ese simbolo como aproximacion, y se marca
`is_approximated=True` para que el reporte final lo desglose honestamente.
"""

from __future__ import annotations

import statistics

from app.persistence.models import Side

DEFAULT_FUNDING_INTERVAL_HOURS = 8.0


def build_funding_series(
    bar_open_times: list[int], real_events: list[tuple[int, float]]
) -> tuple[list[float], list[bool]]:
    """Devuelve (tasas, es_aproximado) alineados 1:1 con `bar_open_times`
    (ordenados ascendentemente). `real_events` son (fundingTime_ms, rate)
    verificados contra la API real."""
    if not bar_open_times:
        return [], []
    if not real_events:
        return [0.0] * len(bar_open_times), [True] * len(bar_open_times)

    sorted_events = sorted(real_events)
    median_rate = statistics.median(rate for _, rate in sorted_events)
    earliest_real_ts = sorted_events[0][0]

    rates: list[float] = []
    approx_flags: list[bool] = []
    idx = 0
    current_rate = median_rate
    for t in bar_open_times:
        while idx < len(sorted_events) and sorted_events[idx][0] <= t:
            current_rate = sorted_events[idx][1]
            idx += 1
        is_approx = t < earliest_real_ts
        rates.append(median_rate if is_approx else current_rate)
        approx_flags.append(is_approx)
    return rates, approx_flags


def funding_cost_for_bar(
    notional: float,
    rate: float,
    side: Side,
    bar_duration_hours: float,
    funding_interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS,
) -> float:
    """Costo de funding (positivo = la posicion paga) para una vela, con la
    tasa prorrateada segun la duracion de la vela respecto al intervalo de
    funding del contrato. Convencion: funding positivo -> LONG paga a SHORT."""
    if funding_interval_hours <= 0:
        funding_interval_hours = DEFAULT_FUNDING_INTERVAL_HOURS
    prorated_rate = rate * (bar_duration_hours / funding_interval_hours)
    sign = 1.0 if side == Side.LONG else -1.0
    return notional * prorated_rate * sign
