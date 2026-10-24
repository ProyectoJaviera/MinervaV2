"""Motor compartido de SL/TP/liquidacion -- backtest (modo vela) y paper
trading en vivo (modo tick), subfase 3.1 de `docs/FASE3_PLAN.md` (punto 3).

Las 3 funciones de modo VELA (`order_adverse_thresholds`,
`check_adverse_bar`, `check_favorable_tp_bar`) son una extraccion PURA de
`app/backtesting/engine.py` (antes `_order_adverse_thresholds`,
`_check_adverse`, `_check_favorable_tp`, privadas de ese modulo) --
mismo comportamiento exacto, solo renombradas (sin el guion bajo inicial,
publicas ahora) y movidas aqui para poder compartirse con el modo vivo.
El motor de backtest las sigue llamando igual; sus tests existentes no se
tocaron y siguen pasando, confirmando que no hubo regresion.

Las funciones nuevas en modo TICK (`check_adverse_tick`,
`check_favorable_tp_tick`) resuelven el mismo problema para un UNICO
precio a la vez -- sin la nocion de "apertura de vela"/gap que tiene el
modo vela (un tick no tiene rango, es un solo punto). Devuelven siempre el
precio REAL observado en el tick que disparo la condicion, nunca el
umbral nominal: en modo tick, cada disparo es, en los hechos, como un gap
del modo vela (solo se observan precios discretos de tick, nunca una
trayectoria continua de precio), asi que el precio realmente observado es
siempre el relleno mas honesto disponible -- igual que el modo vela ya
ejecuta al OPEN real en vez del umbral nominal cuando hay un gap.

Ambos modos comparten `order_adverse_thresholds`: la cercania de un
umbral al precio de entrada es una propiedad de los UMBRALES mismos
(SL vs. liquidacion), no de como se observa el precio despues.
"""

from __future__ import annotations


def order_adverse_thresholds(
    is_long: bool, sl_threshold: float, liq_threshold: float
) -> list[tuple[str, float]]:
    """Los umbrales adversos (SL y liquidacion), el MAS CERCANO al precio de entrada primero.

    Ese es el que se alcanzaria primero si el precio se mueve en contra de forma
    monotona. Para LONG, "mas cerca" significa precio mas ALTO; para SHORT, mas BAJO.

    Args:
        is_long: True si la posicion es LONG.
        sl_threshold: precio del SL vigente.
        liq_threshold: precio de liquidacion.

    Returns:
        Lista `[(nombre, precio), ...]` con `SL` y `LIQUIDATION`.
    """
    pairs = [("SL", sl_threshold), ("LIQUIDATION", liq_threshold)]
    pairs.sort(key=lambda p: p[1], reverse=is_long)
    return pairs


def check_adverse_bar(
    is_long: bool,
    bar_open: float,
    last_low: float,
    last_high: float,
    mark_low: float,
    mark_high: float,
    sl_threshold: float,
    liq_threshold: float,
) -> tuple[str, float] | None:
    """Revisa SL y liquidacion contra una vela, en orden de cercania, con ejecucion al OPEN en gap.

    Si la vela ya abrio mas alla del umbral se ejecuta a ese OPEN (nunca a un precio que no
    se transo). El SL usa el rango de LAST_PRICE y la liquidacion el de MARK_PRICE. Modo vela.

    Args:
        is_long: True si la posicion es LONG.
        bar_open: apertura de la vela.
        last_low: minimo LAST_PRICE de la vela.
        last_high: maximo LAST_PRICE de la vela.
        mark_low: minimo MARK_PRICE de la vela.
        mark_high: maximo MARK_PRICE de la vela.
        sl_threshold: precio del SL vigente.
        liq_threshold: precio de liquidacion.

    Returns:
        La tupla `(motivo, precio de cierre)`, o `None` si ninguno se activo.
    """
    adverse_extreme = {"SL": last_low if is_long else last_high,
                        "LIQUIDATION": mark_low if is_long else mark_high}
    for name, threshold in order_adverse_thresholds(is_long, sl_threshold, liq_threshold):
        gapped = bar_open <= threshold if is_long else bar_open >= threshold
        if gapped:
            return name, bar_open
        extreme = adverse_extreme[name]
        hit = extreme <= threshold if is_long else extreme >= threshold
        if hit:
            return name, threshold
    return None


def check_favorable_tp_bar(
    is_long: bool, bar_open: float, last_high: float, last_low: float, tp_threshold: float
) -> float | None:
    """Revisa el TP contra una vela con la misma logica de gap que el lado adverso.

    Si la vela ya abrio mas alla del TP se ejecuta a ese OPEN (nunca peor que el nominal).

    Args:
        is_long: True si la posicion es LONG.
        bar_open: apertura de la vela.
        last_high: maximo LAST_PRICE de la vela.
        last_low: minimo LAST_PRICE de la vela.
        tp_threshold: precio del TP.

    Returns:
        El precio de ejecucion, o `None` si el TP no se toco.
    """
    gapped = bar_open >= tp_threshold if is_long else bar_open <= tp_threshold
    if gapped:
        return bar_open
    extreme = last_high if is_long else last_low
    hit = extreme >= tp_threshold if is_long else extreme <= tp_threshold
    return tp_threshold if hit else None


def check_adverse_tick(
    is_long: bool,
    last_price: float,
    mark_price: float,
    sl_threshold: float,
    liq_threshold: float,
) -> tuple[str, float] | None:
    """Equivalente de `check_adverse_bar` para un unico tick (sin gap ni rango de vela).

    Revisa el SL contra `last_price` y la liquidacion contra `mark_price`, en el mismo
    orden de cercania, y devuelve el precio REAL observado en el tick. Modo tick (en vivo).

    Args:
        is_long: True si la posicion es LONG.
        last_price: ultimo precio del tick.
        mark_price: precio de marca.
        sl_threshold: precio del SL vigente.
        liq_threshold: precio de liquidacion.

    Returns:
        La tupla `(motivo, precio del tick)`, o `None` si ninguno se activo.
    """
    price_for = {"SL": last_price, "LIQUIDATION": mark_price}
    for name, threshold in order_adverse_thresholds(is_long, sl_threshold, liq_threshold):
        price = price_for[name]
        hit = price <= threshold if is_long else price >= threshold
        if hit:
            return name, price
    return None


def check_favorable_tp_tick(is_long: bool, price: float, tp_threshold: float) -> float | None:
    """Equivalente de `check_favorable_tp_bar` para un unico tick.

    Args:
        is_long: True si la posicion es LONG.
        price: precio del tick.
        tp_threshold: precio del TP.

    Returns:
        El precio del tick (puede superar el TP nominal), o `None` si no lo cruzo.
    """
    hit = price >= tp_threshold if is_long else price <= tp_threshold
    return price if hit else None


def tp_tick_fill_price(
    is_long: bool, observed_price: float, tp_threshold: float, gap_tolerance_frac: float
) -> float:
    """Precio de relleno de un TP en modo tick, una vez confirmado que el tick lo cruzo.

    Es el nominal (el precio del TP), salvo un hueco evidente: si el tick supera el TP en
    mas de `gap_tolerance_frac` se usa el precio observado. Rellenar siempre al observado
    seria optimista: un tick que cruza el TP casi nunca es el precio real de ejecucion.

    Args:
        is_long: True si la posicion es LONG.
        observed_price: precio del tick.
        tp_threshold: precio del TP.
        gap_tolerance_frac: tolerancia como fraccion (`TP_GAP_TOLERANCE_PCT`).

    Returns:
        El precio de relleno.
    """
    if is_long:
        evident_gap = observed_price > tp_threshold * (1 + gap_tolerance_frac)
    else:
        evident_gap = observed_price < tp_threshold * (1 - gap_tolerance_frac)
    return observed_price if evident_gap else tp_threshold


def advance_trailing_stop(
    is_long: bool,
    best_price: float,
    effective_stop: float,
    trailing_distance: float,
    favorable_extreme: float,
) -> tuple[float, float]:
    """Avanza el trailing con el extremo favorable de un tick o de una vela.

    El stop solo se mueve a favor. Debe llamarse DESPUES de revisar SL y TP con el mismo
    dato, igual que el backtest.

    Args:
        is_long: True si la posicion es LONG.
        best_price: mejor precio alcanzado hasta ahora.
        effective_stop: stop vigente.
        trailing_distance: distancia fija del trailing, en precio.
        favorable_extreme: maximo (LONG) o minimo (SHORT) del tick o vela.

    Returns:
        La tupla `(mejor precio, stop efectivo)` actualizada.
    """
    if is_long:
        best = max(best_price, favorable_extreme)
        effective = max(effective_stop, best - trailing_distance)
    else:
        best = min(best_price, favorable_extreme)
        effective = min(effective_stop, best + trailing_distance)
    return best, effective
