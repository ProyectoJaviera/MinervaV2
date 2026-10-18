"""E2E: el ciclo completo de una vela de 4 h (senal -> sombra -> etiqueta -> brazo -> monitor ->
cierre) con las estrategias reales, el servicio de decision real y el brazo real; solo el exterior
es simulado (Anthropic, Bitunix, WebSocket, Telegram). Ver tests/e2e/world.py."""

from __future__ import annotations

import pytest

from app.llm.client import LlmHttpError, LlmTimeoutError
from app.persistence.models import Side
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256
from tests.e2e.world import Market, World

TAKER = 0.0006  # comision taker por defecto (VIP0)
SLIPPAGE = 0.0005  # 5 bps por defecto


def net_pnl(side: Side, qty: float, entry: float, exit_: float) -> float:
    """PnL neto esperado, calculado aqui con las formulas de comision y deslizamiento (sin funding),
    NO copiado del codigo bajo prueba."""
    gross = qty * (exit_ - entry) * (1 if side == Side.LONG else -1)
    fees = TAKER * qty * entry + TAKER * qty * exit_
    slippage = SLIPPAGE * qty * entry + SLIPPAGE * qty * exit_
    return gross - fees - slippage


async def long_and_short_world(make_world, **settings) -> World:
    """BTCUSDT con un cruce 4h LONG, ETHUSDT con uno SHORT y SOLUSDT sin senal."""
    world = await make_world(**settings)
    await world.seed_market({
        "BTCUSDT": Market(100.0, Side.LONG),
        "ETHUSDT": Market(100.0, Side.SHORT),
        "SOLUSDT": Market(100.0),
    })
    return world


# --- LONG y SHORT: la cadena completa ------------------------------------------------------------


async def test_a_long_candle_goes_from_signal_to_a_paper_trade_closed_at_take_profit(make_world):
    world = await long_and_short_world(make_world)

    candidates = await world.cycle()

    # 1) SENAL: el generador real evaluo todo el universo; solo BTC y ETH tienen accion
    assert sorted((c.symbol, c.side.value) for c in candidates) == [
        ("BTCUSDT", "LONG"), ("ETHUSDT", "SHORT"),
    ]
    signals = await world.query("SELECT symbol, strategy, signal, status FROM signals")
    assert len(signals) == 3 * 7  # una fila por (simbolo, estrategia, timeframe), HOLD incluido
    actionable = {
        (s["symbol"], s["strategy"], s["signal"]) for s in signals if s["signal"] != "HOLD"
    }
    assert actionable == {
        ("BTCUSDT", "ema_cross_9_21", "LONG"), ("BTCUSDT", "trend_atr_stop_9_21_50", "LONG"),
        ("ETHUSDT", "ema_cross_9_21", "SHORT"), ("ETHUSDT", "trend_atr_stop_9_21_50", "SHORT"),
    }
    assert {s["status"] for s in signals if s["signal"] != "HOLD"} == {"PENDING"}

    # 2) SOMBRA: una por senal agrupada, con las dos estrategias que coincidieron
    btc_shadow = next(s for s in await world.shadows() if s["symbol"] == "BTCUSDT")
    assert btc_shadow["status"] == "OPEN" and btc_shadow["side"] == "LONG"
    assert btc_shadow["contributing_strategies"] == '["ema_cross_9_21", "trend_atr_stop_9_21_50"]'
    assert btc_shadow["signal_group_key"].startswith("BTCUSDT|LONG|")

    # 3) ETIQUETA: una fila de la medicion con el hash congelado, a segundos del cierre de la vela
    log = next(
        r for r in await world.query("SELECT * FROM llm_logs")
        if r["signal_group_key"] == btc_shadow["signal_group_key"]
    )
    assert (log["fase"], log["status"], log["decision"]) == ("MEDICION", "OK", "APROBAR")
    assert log["prompt_sha256"] == FROZEN_PROMPT_SHA256
    assert 0 <= log["decision_delay_s"] < 60 and log["cost_usd"] > 0
    assert btc_shadow["llm_decision"] == "APROBADA"
    assert [c["symbol"] for c in world.llm.calls].count("SOLUSDT") == 0  # sin senal no hay consulta

    # 4) BRAZO: abrio la posicion paper, con los niveles de respaldo (SL 5 %, TP 10 %)
    arm = next(r for r in await world.arm_rows() if r["symbol"] == "BTCUSDT")
    assert (arm["outcome"], arm["reason"]) == ("OPENED", None)
    trade = next(t for t in await world.trades("OPEN") if t["symbol"] == "BTCUSDT")
    assert arm["trade_id"] == trade["id"]
    assert trade["decision_source"] == "LLM_APROBADA" and trade["strategy"] == "ema_cross_9_21"
    assert (trade["margin_usdt"], trade["leverage"]) == (5.0, 10)
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["sl_price"] == pytest.approx(95.0) and trade["tp_price"] == pytest.approx(110.0)
    # entrada x (1 - 1/10 + margen de mantenimiento)
    assert trade["liq_price"] == pytest.approx(90.5)

    # 5) MONITOR: ticks sin cruzar niveles no cierran nada; el que cruza el TP cierra al nominal
    await world.sync()
    for price in (101.0, 104.0, 109.0):
        await world.tick("BTCUSDT", price)
    assert (await world.trades("CLOSED")) == []
    await world.tick("BTCUSDT", 110.1)  # dentro de la tolerancia del 0.2 %: relleno nominal

    # 6) CIERRE: la posicion paper Y su sombra cierran por TP con la misma fuente
    closed = (await world.trades("CLOSED"))[0]
    assert (closed["close_reason"], closed["fill_source"]) == ("TP", "TICK")
    assert closed["exit_price"] == pytest.approx(110.0)
    expected = net_pnl(Side.LONG, closed["qty"], closed["entry_price"], 110.0)
    assert closed["pnl_net_usdt"] == pytest.approx(expected, abs=1e-6)
    assert expected > 4.5  # 10 USDT de recorrido x 0.5 de cantidad, menos costos
    shadow_closed = next(s for s in await world.shadows("CLOSED") if s["symbol"] == "BTCUSDT")
    assert shadow_closed["close_reason"] == "TP"

    # 7) CUENTA Y RIESGO: el saldo realizado es el capital mas el PnL; una ganancia limpia la racha
    assert await world.backend.get_balance() == pytest.approx(100.0 + closed["pnl_net_usdt"])
    assert await world.state("consecutive_losses") in (None, "0")
    health = (await world.get("/health")).json()
    assert health["paper_execution"]["opened_today"] == 2
    assert health["llm_labeling"]["labeled_today"] == 2
    assert health["llm_labeling"]["frozen_hash_ok"] is True and health["status"] == "ok"


async def test_a_short_candle_goes_from_signal_to_a_paper_trade_closed_at_stop_loss(make_world):
    world = await long_and_short_world(make_world)
    await world.cycle()
    await world.sync()

    shadow = next(s for s in await world.shadows() if s["symbol"] == "ETHUSDT")
    trade = next(t for t in await world.trades("OPEN") if t["symbol"] == "ETHUSDT")
    assert shadow["side"] == "SHORT" and trade["side"] == "SHORT"
    assert trade["decision_source"] == "LLM_APROBADA"
    assert trade["sl_price"] == pytest.approx(105.0) and trade["tp_price"] == pytest.approx(90.0)
    assert trade["liq_price"] == pytest.approx(109.5)

    for price in (100.5, 103.0, 104.9):  # contra el corto pero sin tocar el SL
        await world.tick("ETHUSDT", price)
    assert not [t for t in await world.trades("CLOSED") if t["symbol"] == "ETHUSDT"]
    await world.tick("ETHUSDT", 105.5)  # cruza el SL: peor caso, se rellena al precio OBSERVADO

    closed = next(t for t in await world.trades("CLOSED") if t["symbol"] == "ETHUSDT")
    assert (closed["close_reason"], closed["fill_source"]) == ("SL", "TICK")
    assert closed["exit_price"] == pytest.approx(105.5)
    expected = net_pnl(Side.SHORT, closed["qty"], closed["entry_price"], 105.5)
    assert closed["pnl_net_usdt"] == pytest.approx(expected, abs=1e-6)
    assert expected < -2.8
    assert await world.state("consecutive_losses") == "1"  # la perdida alimenta el circuit breaker
    assert await world.backend.get_balance() == pytest.approx(100.0 + closed["pnl_net_usdt"])
    shadow_closed = next(s for s in await world.shadows("CLOSED") if s["symbol"] == "ETHUSDT")
    assert shadow_closed["close_reason"] == "SL"


async def test_the_same_candle_is_never_processed_twice(make_world):
    world = await long_and_short_world(make_world)
    first = await world.cycle()
    snapshot = {
        table: len(await world.query(f"SELECT * FROM {table}"))
        for table in ("signals", "shadow_trades", "llm_logs", "paper_execution_log", "trades")
    }

    second = await world.cycle()  # misma vela: nada nuevo

    assert len(first) == 2 and second == []
    assert snapshot == {
        table: len(await world.query(f"SELECT * FROM {table}"))
        for table in ("signals", "shadow_trades", "llm_logs", "paper_execution_log", "trades")
    }
    assert len(world.llm.calls) == 2  # una consulta por grupo, no por ciclo


# --- lo que NO abre posicion ---------------------------------------------------------------------


async def test_a_rejected_signal_is_measured_as_a_shadow_but_never_opens_a_position(make_world):
    world = await long_and_short_world(make_world)
    world.llm.decisions["BTCUSDT"] = "RECHAZAR"

    await world.cycle()
    await world.sync()

    btc = next(s for s in await world.shadows() if s["symbol"] == "BTCUSDT")
    assert btc["llm_decision"] == "RECHAZADA" and btc["status"] == "OPEN"
    assert [r["symbol"] for r in await world.arm_rows()] == ["ETHUSDT"]  # el brazo no la ve
    assert [t["symbol"] for t in await world.trades()] == ["ETHUSDT"]
    # La sombra rechazada SIGUE midiendose: es la contrapartida de APROBADA en la comparacion.
    await world.tick("BTCUSDT", 110.1)
    btc_closed = next(s for s in await world.shadows("CLOSED") if s["symbol"] == "BTCUSDT")
    assert btc_closed["close_reason"] == "TP" and btc_closed["pnl_net_usdt"] > 4.5
    assert [t for t in await world.trades() if t["symbol"] == "BTCUSDT"] == []


@pytest.mark.parametrize("failure", [LlmTimeoutError("sin respuesta"), LlmHttpError("HTTP 529")])
async def test_an_llm_failure_leaves_the_signal_without_label_and_opens_nothing(
    make_world, failure,
):
    world = await long_and_short_world(make_world)
    world.llm.decisions["BTCUSDT"] = failure

    await world.cycle()

    btc = next(s for s in await world.shadows() if s["symbol"] == "BTCUSDT")
    assert btc["llm_decision"] == "SIN_LLM" and btc["status"] == "OPEN"  # nunca RECHAZADA
    log = next(r for r in await world.query("SELECT * FROM llm_logs")
               if r["signal_group_key"] == btc["signal_group_key"])
    assert log["status"] in ("TIMEOUT", "ERROR_HTTP") and log["decision"] is None
    assert [r["symbol"] for r in await world.arm_rows()] == ["ETHUSDT"]
    health = (await world.get("/health")).json()
    assert health["llm_labeling"]["failed_today"] == 1
    assert health["llm_labeling"]["labeled_today"] == 1  # la de ETH sigue su camino


async def test_a_decision_that_arrives_after_60_seconds_is_labeled_but_not_executed(make_world):
    world = await make_world()
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)}, close_age_s=90)

    await world.cycle()

    shadow = (await world.shadows())[0]
    assert shadow["llm_decision"] == "APROBADA"  # la medicion si cuenta (llega antes de 1800 s)
    arm = (await world.arm_rows())[0]
    assert (arm["outcome"], arm["reason"]) == ("SKIPPED", "LATE_DECISION")
    assert arm["decision_delay_s"] > 60 and await world.trades() == []
