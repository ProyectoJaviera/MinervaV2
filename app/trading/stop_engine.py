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
    """[(nombre, precio), ...] de los umbrales adversos (SL, liquidacion),
    el MAS CERCANO al precio de entrada primero -- ese es el que se
    alcanzaria primero si el precio se mueve en contra de forma monotona.
    Para LONG, "mas cerca" = precio mas ALTO; para SHORT, mas BAJO."""
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
    """Revisa SL y liquidacion en orden de cercania, con ejecucion al OPEN
    si la vela ya abrio mas alla del umbral (gap). Devuelve
    `(razon, precio_de_cierre)` o `None` si ninguno se activo. Modo VELA --
    necesita el rango high/low de la vela en las series LAST_PRICE (SL) y
    MARK_PRICE (liquidacion)."""
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
    """Revisa el TP con la misma logica de gap que `check_adverse_bar`: si
    la vela ya abrio mas alla del TP, se ejecuta a ese OPEN (mejor para la
    posicion que el precio nominal del TP, nunca peor). Modo VELA."""
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
    """Equivalente a `check_adverse_bar` para un unico tick (sin gap, sin
    rango de vela): revisa SL contra `last_price` y liquidacion contra
    `mark_price`, en el mismo orden de cercania. Devuelve
    `(razon, precio_real_del_tick)` o `None`. Modo TICK -- paper trading en
    vivo."""
    price_for = {"SL": last_price, "LIQUIDATION": mark_price}
    for name, threshold in order_adverse_thresholds(is_long, sl_threshold, liq_threshold):
        price = price_for[name]
        hit = price <= threshold if is_long else price >= threshold
        if hit:
            return name, price
    return None


def check_favorable_tp_tick(is_long: bool, price: float, tp_threshold: float) -> float | None:
    """Equivalente a `check_favorable_tp_bar` para un unico tick. Devuelve
    el precio real del tick (puede superar el TP nominal) o `None`. Modo
    TICK."""
    hit = price >= tp_threshold if is_long else price <= tp_threshold
    return price if hit else None
