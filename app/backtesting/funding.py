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
    """Alinea el funding real con las velas del backtest, aproximando donde no hay dato.

    Antes del primer evento real de un simbolo se usa la MEDIANA del funding real observado
    y se marca la vela como aproximada; sin ningun evento real todas valen 0.0 y estan
    marcadas como aproximadas.

    Args:
        bar_open_times: `open_time` de las velas, en ms Unix y ascendentes.
        real_events: eventos reales `(fundingTime en ms, tasa)`.

    Returns:
        La tupla `(tasas, es_aproximado)`, alineadas 1:1 con `bar_open_times`.
    """
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
    """Costo de funding de una vela, con la tasa prorrateada por su duracion.

    Convencion: con tasa positiva el LONG paga al SHORT. Se prorratea linealmente respecto
    al intervalo de funding del contrato (en vez de aplicar eventos puntuales).

    Args:
        notional: nocional de la posicion, en USDT.
        rate: tasa de funding vigente en la vela.
        side: direccion de la posicion.
        bar_duration_hours: duracion de la vela, en horas.
        funding_interval_hours: intervalo de funding del contrato (8 h si es <= 0).

    Returns:
        El costo en USDT; positivo significa que la posicion paga.
    """
    if funding_interval_hours <= 0:
        funding_interval_hours = DEFAULT_FUNDING_INTERVAL_HOURS
    prorated_rate = rate * (bar_duration_hours / funding_interval_hours)
    sign = 1.0 if side == Side.LONG else -1.0
    return notional * prorated_rate * sign
