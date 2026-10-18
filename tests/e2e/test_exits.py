"""E2E: las salidas que decide el monitor de posiciones sobre posiciones abiertas por la cadena
completa -- trailing (LONG y SHORT, con la estrategia de tendencia ATR) y liquidacion, que solo
ocurre si el mark por REST la confirma. El TP y el SL estan en test_full_cycle.py."""

from __future__ import annotations

import pytest

from app.persistence.models import Side
from tests.e2e.test_full_cycle import net_pnl
from tests.e2e.world import Market


@pytest.mark.parametrize(
    ("side", "symbol"), [(Side.LONG, "BTCUSDT"), (Side.SHORT, "ETHUSDT")],
)
async def test_the_trailing_stop_follows_the_price_and_closes_on_the_retrace(
    make_world, side, symbol,
):
    # Solo la estrategia de tendencia ATR es elegible: la senal se atribuye a ella y trae su SL y
    # su trailing por ATR (sin TP), no los porcentajes de respaldo.
    world = await make_world(REAL_ACCOUNT_ELIGIBLE_STRATEGIES="trend_atr_stop_9_21_50")
    await world.seed_market({symbol: Market(100.0, side)})
    sign = 1 if side == Side.LONG else -1

    await world.cycle()
    await world.sync()

    trade = (await world.trades("OPEN"))[0]
    assert trade["strategy"] == "trend_atr_stop_9_21_50" and trade["tp_price"] is None
    distance = trade["trailing_distance"]
    assert distance == pytest.approx(abs(100.0 - trade["sl_price"]), rel=1e-6)
    assert trade["effective_stop"] == pytest.approx(trade["sl_price"])
    shadow = (await world.shadows("OPEN"))[0]
    assert shadow["trailing_distance"] == pytest.approx(distance)  # la sombra: el mismo trailing

    for pct in (1.0, 3.0, 5.0):  # el precio avanza a favor: el stop sube (o baja) detras
        await world.tick(symbol, 100.0 * (1 + sign * pct / 100))
    best = 100.0 * (1 + sign * 0.05)
    await world.sync()  # persiste el estado de riesgo en memoria
    moved = (await world.trades("OPEN"))[0]
    assert moved["best_price"] == pytest.approx(best)
    assert moved["effective_stop"] == pytest.approx(best - sign * distance)

    await world.tick(symbol, best - sign * distance / 2)  # retroceso que NO cruza el stop
    assert await world.trades("CLOSED") == []

    exit_price = best - sign * (distance + 0.2)
    await world.tick(symbol, exit_price)  # cruza el stop de trailing: precio observado

    closed = (await world.trades("CLOSED"))[0]
    assert (closed["close_reason"], closed["fill_source"]) == ("TRAILING", "TICK")
    assert closed["exit_price"] == pytest.approx(exit_price)
    expected = net_pnl(side, closed["qty"], closed["entry_price"], exit_price)
    assert closed["pnl_net_usdt"] == pytest.approx(expected, abs=1e-6) and expected > 0
    assert (await world.shadows("CLOSED"))[0]["close_reason"] == "TRAILING"
    assert await world.state("consecutive_losses") == "0"  # ganar con trailing no suma a la racha


async def test_a_liquidation_needs_the_rest_mark_to_confirm_it(make_world):
    # SL de respaldo del 10 % (100 % del margen a 10x) y tope 100 %: el SL queda DEBAJO del precio
    # de liquidacion, asi que es la liquidacion lo que se cruza primero. Solo para este escenario.
    world = await make_world(BACKTEST_FALLBACK_SL_PCT=0.10, LIVE_SL_MARGIN_CAP_PCT=100.0)
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})
    await world.cycle()
    await world.sync()
    trade = (await world.trades("OPEN"))[0]
    assert trade["sl_price"] == pytest.approx(90.0) and trade["liq_price"] == pytest.approx(90.5)

    # El ULTIMO precio cruza la liquidacion, pero el mark no: no se liquida (ni la sombra).
    await world.tick("BTCUSDT", 90.3, mark=95.0)
    assert await world.trades("CLOSED") == [] and await world.shadows("CLOSED") == []

    # Si la consulta del mark falla tampoco se liquida (se reintenta mas tarde).
    world.expire_monitor_caches()
    world.rest.fail_tickers = True
    await world.tick("BTCUSDT", 90.3)
    assert await world.trades("CLOSED") == []
    world.rest.fail_tickers = False

    # Con el mark cruzando la liquidacion, cierra al MARK, por LIQUIDATION.
    world.expire_monitor_caches()
    await world.tick("BTCUSDT", 90.2, mark=90.1)

    closed = (await world.trades("CLOSED"))[0]
    assert (closed["close_reason"], closed["fill_source"]) == ("LIQUIDATION", "TICK")
    assert closed["exit_price"] == pytest.approx(90.1)
    expected = net_pnl(Side.LONG, closed["qty"], closed["entry_price"], 90.1)
    assert closed["pnl_net_usdt"] == pytest.approx(expected, abs=1e-6)
    assert closed["pnl_net_usdt"] < -closed["margin_usdt"] * 0.95  # casi todo el margen
    assert (await world.shadows("CLOSED"))[0]["close_reason"] == "LIQUIDATION"
    assert world.monitor.liquidation_confirmations >= 2
    assert await world.state("consecutive_losses") == "1"
