"""E2E: apagar y volver a arrancar el sistema sobre la MISMA base. Lo que paso durante la caida se
reproduce con velas de 1 minuto (SL, TP, liquidacion, trailing); lo que no cruzo nada sigue abierto;
nada se mide ni se avisa dos veces; y lo que quedo a medias (un reclamo del brazo, una llamada al
LLM en vuelo) se resuelve sin duplicar ni romper. La caida se simula retrocediendo las marcas de
tiempo, porque no se puede esperar media hora en una prueba."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.models import Side
from app.persistence.repositories import ohlcv_repo, system_state_repo
from app.persistence.repositories import paper_execution_repo as arm_log
from app.trading.position_monitor import MONITOR_LAST_SEEN_KEY
from tests.e2e import series
from tests.e2e.world import Market, World

BAR = series.MINUTE_MS


def minute_start(now_ms: int, minutes_ago: int) -> int:
    return ((now_ms - minutes_ago * BAR) // BAR) * BAR


async def four_open_positions(make_world) -> World:
    world = await make_world(MAX_SIMULTANEOUS_POSITIONS=4, MAX_SAME_DIRECTION_POSITIONS=4)
    await world.seed_market({
        "BTCUSDT": Market(100.0, Side.LONG),  # su SL se cruza durante la caida
        "ETHUSDT": Market(100.0, Side.SHORT),  # idem, en corto
        "SOLUSDT": Market(100.0, Side.LONG),  # su TP se cruza durante la caida
        "XRPUSDT": Market(100.0, Side.LONG),  # no cruza nada
    })
    await world.cycle()
    await world.sync()
    assert len(await world.trades("OPEN")) == 4 and len(await world.shadows("OPEN")) == 4
    await world.notifications()  # las cuatro aperturas ya fueron avisadas
    return world


async def go_back_in_time(db, now_ms: int, minutes: int, last_seen_minutes: int) -> None:
    """Hace parecer que las posiciones y sombras se abrieron hace `minutes` y que el monitor vio por
    ultima vez el mercado hace `last_seen_minutes`."""
    opened = datetime.fromtimestamp((now_ms - minutes * BAR) / 1000, tz=UTC).isoformat()
    await db.execute("UPDATE trades SET opened_at = ?", (opened,))
    await db.execute("UPDATE shadow_trades SET opened_at = ?", (opened,))
    await system_state_repo.set_state(
        db, MONITOR_LAST_SEEN_KEY, str(now_ms - last_seen_minutes * BAR)
    )


async def seed_downtime(db, now_ms: int, symbol: str, overrides: dict | None = None) -> None:
    await ohlcv_repo.upsert_bars(db, series.minute_bars(
        symbol, first_open_ms=minute_start(now_ms, 30),
        last_open_ms=minute_start(now_ms, 0) + 2 * BAR,  # +2: holgura si cambia el minuto
        overrides=overrides,
    ))


async def test_the_downtime_is_replayed_with_1m_candles_and_closes_what_the_market_crossed(
    make_world,
):
    world = await four_open_positions(make_world)
    await world.power_off()
    now_ms = int(time.time() * 1000)
    async with world.offline_db() as db:
        await go_back_in_time(db, now_ms, minutes=30, last_seen_minutes=20)
        crash = minute_start(now_ms, 5)
        await seed_downtime(db, now_ms, "BTCUSDT", {crash: {"low": 94.0, "close": 94.5}})
        spike = minute_start(now_ms, 4)
        await seed_downtime(db, now_ms, "ETHUSDT", {spike: {"high": 106.0, "close": 105.5}})
        rally = minute_start(now_ms, 3)
        await seed_downtime(db, now_ms, "SOLUSDT", {rally: {"high": 111.0, "close": 110.5}})
        await seed_downtime(db, now_ms, "XRPUSDT")

    await world.power_on(now_ms=now_ms)

    outcomes = sorted(outcome for _id, outcome in world.reconcile_outcomes)
    assert outcomes == ["ABIERTA"] * 2 + ["CERRADA_SL"] * 4 + ["CERRADA_TP"] * 2
    closed = {t["symbol"]: t for t in await world.trades("CLOSED")}
    assert set(closed) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    # Precio NOMINAL del nivel (convencion del backtest), no el extremo de la vela; CANDLE_RECON.
    assert (closed["BTCUSDT"]["close_reason"], closed["BTCUSDT"]["exit_price"]) == (
        "SL", pytest.approx(95.0),
    )
    assert (closed["ETHUSDT"]["close_reason"], closed["ETHUSDT"]["exit_price"]) == (
        "SL", pytest.approx(105.0),
    )
    assert (closed["SOLUSDT"]["close_reason"], closed["SOLUSDT"]["exit_price"]) == (
        "TP", pytest.approx(110.0),
    )
    assert {t["fill_source"] for t in closed.values()} == {"CANDLE_RECON"}
    # El cierre queda fechado en el MINUTO del cruce (durante la caida), no al arrancar.
    for symbol, minutes_ago in (("BTCUSDT", 5), ("ETHUSDT", 4), ("SOLUSDT", 3)):
        closed_at = datetime.fromisoformat(closed[symbol]["closed_at"])
        assert closed_at == datetime.fromtimestamp(
            (minute_start(now_ms, minutes_ago) + BAR) / 1000, tz=UTC
        )
    # Las sombras corren la misma suerte (la medicion no pierde sus resultados por la caida).
    shadows = {s["symbol"]: s for s in await world.shadows("CLOSED")}
    assert {k: v["close_reason"] for k, v in shadows.items()} == {
        "BTCUSDT": "SL", "ETHUSDT": "SL", "SOLUSDT": "TP",
    }
    assert [t["symbol"] for t in await world.trades("OPEN")] == ["XRPUSDT"]
    assert [s["symbol"] for s in await world.shadows("OPEN")] == ["XRPUSDT"]
    # Dos perdidas y una ganancia en el orden de cierre alimentan la racha del circuit breaker.
    assert await world.state("consecutive_losses") in {"0", "1", "2"}


async def test_after_a_restart_nothing_is_measured_or_announced_twice(make_world):
    world = await four_open_positions(make_world)
    await world.power_off()
    now_ms = int(time.time() * 1000)
    async with world.offline_db() as db:
        await go_back_in_time(db, now_ms, minutes=30, last_seen_minutes=20)
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            overrides = {minute_start(now_ms, 5): {"low": 94.0}} if symbol == "BTCUSDT" else None
            await seed_downtime(db, now_ms, symbol, overrides)
    await world.power_on(now_ms=now_ms)
    counts = {
        table: len(await world.query(f"SELECT * FROM {table}"))
        for table in ("signals", "shadow_trades", "llm_logs", "paper_execution_log", "trades")
    }

    # La misma vela de 4 h otra vez: ni senales, ni sombras, ni consultas al LLM nuevas.
    assert await world.cycle() == []
    assert counts == {
        table: len(await world.query(f"SELECT * FROM {table}"))
        for table in ("signals", "shadow_trades", "llm_logs", "paper_execution_log", "trades")
    }
    assert len(world.llm.calls) == 4  # las 4 consultas originales, ninguna mas

    # El notificador retoma el estado guardado: avisa el cierre ocurrido durante la caida,
    # una sola vez, y no vuelve a avisar las aperturas.
    notes = await world.notifications()
    assert [n.splitlines()[1].split(" (")[0] for n in notes] == ["Posicion cerrada: BTCUSDT LONG"]
    assert await world.notifications() == []

    # Un segundo reinicio sin caida adicional no cierra nada mas.
    await world.restart()
    assert len(await world.trades("CLOSED")) == 1 and len(await world.trades("OPEN")) == 3
    assert await world.notifications() == []


async def test_the_trailing_stop_survives_a_restart(make_world):
    world = await make_world(REAL_ACCOUNT_ELIGIBLE_STRATEGIES="trend_atr_stop_9_21_50")
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})
    await world.cycle()
    await world.sync()
    distance = (await world.trades("OPEN"))[0]["trailing_distance"]
    for price in (103.0, 105.0):
        await world.tick("BTCUSDT", price)
    await world.sync()  # persiste el stop adelantado
    assert (await world.trades("OPEN"))[0]["effective_stop"] == pytest.approx(105.0 - distance)

    await world.restart()  # sin caida: la ventana de reconciliacion es menor a un minuto
    await world.sync()

    assert [o for _i, o in world.reconcile_outcomes] == ["SIN_VENTANA"] * 2
    assert world.monitor._trades[(False, 1)].effective_stop == pytest.approx(105.0 - distance)
    await world.tick("BTCUSDT", 105.0 - distance / 2)  # retroceso que no cruza el stop heredado
    assert await world.trades("CLOSED") == []
    await world.tick("BTCUSDT", 105.0 - distance - 0.2)  # lo cruza
    closed = (await world.trades("CLOSED"))[0]
    assert closed["close_reason"] == "TRAILING" and closed["pnl_net_usdt"] > 0


async def test_a_claim_left_by_a_crash_is_resolved_at_startup_without_opening_anything(make_world):
    world = await make_world()
    await world.seed_market({"BTCUSDT": Market(100.0)})
    await world.power_off()
    async with world.offline_db() as db:
        # El proceso murio justo despues de reclamar el grupo y antes de abrir la posicion.
        claimed = await arm_log.claim(
            db, signal_group_key="BTCUSDT|LONG|2026-10-18T12:00:00+00:00", shadow_trade_id=None,
            symbol="BTCUSDT", side="LONG", strategy="ema_cross_9_21", decision_delay_s=3.0,
            created_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        assert claimed is True

    await world.power_on()

    row = (await world.arm_rows())[0]
    assert (row["outcome"], row["reason"]) == ("SKIPPED", "ABORTED_RESTART")
    assert await world.trades() == []
    health = (await world.get("/health")).json()
    assert health["paper_execution"]["active"] is True


async def test_a_restart_in_the_middle_of_an_llm_call_leaves_the_signal_unlabeled_not_broken(
    make_world,
):
    world = await make_world()
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})
    world.llm.delay_s = 30.0  # la respuesta no llega antes del apagado

    await world.scheduler.run_cycle()  # abre la sombra y dispara el etiquetado en segundo plano
    await world.wait_until(lambda: bool(world.llm.calls))  # la consulta ya salio...
    await world.power_off(drain=False)  # ...y el proceso se detiene sin esperarla (se cancela)

    await world.power_on()
    world.llm.delay_s = 0.0
    assert await world.cycle() == []  # la vela ya se evaluo: no se reetiqueta ni se reabre

    shadow = (await world.shadows())[0]
    assert shadow["status"] == "OPEN" and shadow["llm_decision"] == "SIN_LLM"
    assert await world.query("SELECT * FROM llm_logs") == []  # sin fila: "omitida", no medida
    assert await world.arm_rows() == [] and await world.trades() == []
    assert (await world.get("/health")).json()["llm_labeling"]["frozen_hash_ok"] is True
