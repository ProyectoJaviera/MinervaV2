"""E2E: cuatro perdidas seguidas activan el circuit breaker; las entradas nuevas quedan omitidas
pero la MEDICION sigue (sombras y etiquetas); se genera UN post-mortem de la racha; y el breaker
se auto-reanuda al terminar el enfriamiento."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.memory.runtime import LessonsRunner
from app.persistence.models import Side
from app.trading import risk_engine
from tests.e2e.world import Market, World

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


async def streak_world(make_world) -> World:
    """Cuatro longs aprobados que quedan abiertos a la vez; limites holgados para que SOLO el
    circuit breaker (no el limite diario ni el stop por drawdown) pueda frenar las entradas."""
    world = await make_world(
        MAX_SIMULTANEOUS_POSITIONS=4, MAX_SAME_DIRECTION_POSITIONS=4, MAX_DAILY_LOSS_PCT=0.5,
        MAX_DRAWDOWN_PCT=0.9, LESSONS_MEMORY_ENABLED=True,
    )
    await world.seed_market({symbol: Market(100.0, Side.LONG) for symbol in SYMBOLS})
    await world.cycle()
    await world.sync()
    assert len(await world.trades("OPEN")) == 4
    return world


async def lose(world: World, symbol: str) -> None:
    await world.tick(symbol, 94.0)  # cruza el SL de 95: perdida


async def test_four_losses_in_a_row_trip_the_breaker_and_block_new_entries_not_the_measurement(
    make_world,
):
    world = await streak_world(make_world)

    for count, symbol in enumerate(SYMBOLS[:3], start=1):
        await lose(world, symbol)
        assert await world.state("consecutive_losses") == str(count)
        assert await risk_engine.get_active_circuit_breaker_until(world.db) is None
    await lose(world, SYMBOLS[3])  # la cuarta perdida seguida

    closed = await world.trades("CLOSED")
    assert [t["close_reason"] for t in closed] == ["SL"] * 4
    assert all(t["pnl_net_usdt"] < 0 for t in closed)
    until = await risk_engine.get_active_circuit_breaker_until(world.db)
    assert until is not None
    assert timedelta(hours=7, minutes=59) < until - datetime.now(UTC) <= timedelta(hours=8)
    assert await world.state("consecutive_losses") == "0"  # la racha "consumida" no re-dispara

    # El estado se ve en todos los canales: Telegram, API y aviso push.
    status = await world.say("/status")
    assert "Circuit breaker: ACTIVO hasta" in status
    await world.login()
    risk = (await world.get("/api/risk")).json()
    assert risk["circuit_breaker"]["active"] is True
    assert "CIRCUIT_BREAKER_ACTIVE" in risk["entries_blocked_by"]
    assert risk["numbers"] is None  # la racha numerica sigue oculta mientras no haya veredicto
    notes = await world.notifications()
    assert any("circuit breaker" in note.lower() for note in notes)
    assert len([n for n in notes if "cerrada" in n.lower()]) >= 4  # una por posicion cerrada

    # Una senal NUEVA: la sombra se abre y se etiqueta (la medicion sigue) pero el brazo la omite.
    await world.seed_market({"DOGEUSDT": Market(100.0, Side.LONG)})
    await world.cycle()
    doge = next(s for s in await world.shadows() if s["symbol"] == "DOGEUSDT")
    assert doge["status"] == "OPEN" and doge["llm_decision"] == "APROBADA"
    row = next(r for r in await world.arm_rows() if r["symbol"] == "DOGEUSDT")
    assert (row["outcome"], row["reason"]) == ("SKIPPED", "RISK_CIRCUIT_BREAKER_ACTIVE")
    assert [t for t in await world.trades() if t["symbol"] == "DOGEUSDT"] == []
    rejection = (await world.query("SELECT * FROM risk_rejections ORDER BY id DESC"))[0]
    assert (rejection["symbol"], rejection["reason"]) == ("DOGEUSDT", "CIRCUIT_BREAKER_ACTIVE")


async def test_the_streak_gets_exactly_one_post_mortem_and_it_stays_hidden_without_a_verdict(
    make_world,
):
    world = await streak_world(make_world)
    for symbol in SYMBOLS:
        await lose(world, symbol)
    runner = LessonsRunner(world.db, world.settings)

    first = await runner.run_once()
    second = await runner.run_once()  # la misma racha: no se duplica

    assert first["postmortem"] is True and second["postmortem"] is False
    reports = await world.query("SELECT * FROM lessons_reports WHERE kind = 'POSTMORTEM'")
    assert len(reports) == 1
    body = json.loads(reports[0]["body_json"])
    assert body["losses"] == 4 and len(body["trade_ids"]) == 4
    assert body["by_side"] == {"LONG": 4} and body["by_close_reason"] == {"SL": 4}
    assert body["in_common"]["side"] == "LONG" and body["in_common"]["close_reason"] == "SL"
    assert body["total_pnl_net_usdt"] < 0

    # El post-mortem lleva PnL: se guarda siempre pero con la puerta cerrada la API no lo muestra.
    await world.login()
    hidden = (await world.get("/api/memory/reports?kind=POSTMORTEM")).json()
    assert hidden["results_visible"] is False and hidden["reports"] == []
    assert str(body["total_pnl_net_usdt"]) not in json.dumps(hidden)


async def test_the_breaker_resumes_by_itself_when_the_cooldown_ends(make_world):
    world = await streak_world(make_world)
    for symbol in SYMBOLS:
        await lose(world, symbol)
    assert await risk_engine.get_active_circuit_breaker_until(world.db) is not None

    # "Pasan" 8 horas: se retrocede la marca (no se espera en tiempo real).
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await world.db.execute(
        "UPDATE system_state SET value = ? WHERE key = 'circuit_breaker_until'", (expired,),
    )
    assert await risk_engine.get_active_circuit_breaker_until(world.db) is None
    await world.seed_market({"ADAUSDT": Market(100.0, Side.LONG)})

    await world.cycle()

    row = next(r for r in await world.arm_rows() if r["symbol"] == "ADAUSDT")
    assert (row["outcome"], row["reason"]) == ("OPENED", None)
    assert [t["symbol"] for t in await world.trades("OPEN")] == ["ADAUSDT"]


async def test_three_losses_and_a_win_never_trip_the_breaker(make_world):
    world = await streak_world(make_world)
    for symbol in SYMBOLS[:3]:
        await lose(world, symbol)
    await world.tick(SYMBOLS[3], 111.0)  # la cuarta cierra en TP: la racha se corta

    assert await world.state("consecutive_losses") == "0"
    assert await risk_engine.get_active_circuit_breaker_until(world.db) is None
    # y por lo tanto no hay racha que analizar
    summary = await LessonsRunner(world.db, world.settings).run_once()
    assert summary["postmortem"] is False
    assert await world.query("SELECT * FROM lessons_reports WHERE kind = 'POSTMORTEM'") == []
