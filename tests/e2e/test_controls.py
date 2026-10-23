"""E2E: pausa, reanudar y kill switch desde Telegram y desde el dashboard. Una pausa DETIENE LA
MEDICION (sin senales, sombras ni etiquetado) pero nunca los cierres de posiciones; el kill switch
ademas bloquea las entradas; todo queda auditado con el canal y el actor, y los demas canales
(estado, API, resumen diario, aviso push) lo reflejan."""

from __future__ import annotations

import pytest

from app.persistence.models import Side
from app.trading import control_state, risk_engine
from tests.e2e.world import Market, World


async def world_with_an_open_position(make_world) -> World:
    """BTC con un long abierto por la cadena completa; ETH con una senal short SIN procesar aun."""
    world = await make_world()
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})
    await world.cycle()
    await world.sync()
    assert len(await world.trades("OPEN")) == 1
    await world.login()
    await world.notifications()  # vacia los avisos de la apertura
    return world


async def new_signal(world: World) -> None:
    """Una senal nueva (otro simbolo) lista para el proximo ciclo."""
    await world.seed_market({"ETHUSDT": Market(100.0, Side.SHORT)})


async def measured_rows(world: World) -> dict[str, int]:
    return {
        table: len(await world.query(f"SELECT * FROM {table}"))
        for table in ("signals", "shadow_trades", "llm_logs", "paper_execution_log")
    }


# --- Telegram ------------------------------------------------------------------------------------


async def test_pause_from_telegram_asks_for_confirmation_then_stops_the_measurement(make_world):
    world = await world_with_an_open_position(make_world)
    await new_signal(world)
    before = await measured_rows(world)

    warning = await world.say("/pause")  # primer paso: solo advierte
    assert "DETIENE LA MEDICION" in warning and "/pause SI" in warning
    assert await control_state.is_scheduler_paused(world.db) is False
    assert await world.query("SELECT * FROM control_audit") == []
    last_cycle = world.scheduler.last_cycle_at

    done = await world.say("/pause SI")  # segundo paso: ejecuta
    assert "PAUSADO" in done and "DETENIDA" in done
    audit = await world.query("SELECT * FROM control_audit")
    assert [(a["channel"], a["actor"], a["action"]) for a in audit] == [
        ("TELEGRAM", "telegram", "pause"),
    ]

    # Con la pausa: el ciclo no evalua nada (ni senales, ni sombras, ni etiquetas, ni brazo).
    assert await control_state.is_scheduler_paused(world.db) is True
    assert await world.cycle() == []
    assert await measured_rows(world) == before
    assert len(world.llm.calls) == 1  # solo la consulta del BTC, anterior a la pausa
    assert world.scheduler.last_cycle_at == last_cycle  # no avanza: /health lo vera "stale"

    # ... pero los cierres de posiciones SIGUEN (el monitor no esta pausado).
    await world.tick("BTCUSDT", 110.1)
    assert [t["close_reason"] for t in await world.trades("CLOSED")] == ["TP"]

    # Los demas canales lo reflejan.
    health = (await world.get("/health")).json()
    assert health["control"] == {"paused": True, "kill_switch_active": False}
    risk = (await world.get("/api/risk")).json()
    assert risk["paused"] is True and risk["measurement_stopped"] is True
    assert "SCHEDULER_PAUSED" in risk["entries_blocked_by"]
    assert "Scheduler pausado: SI (la medicion esta detenida)" in await world.say("/status")
    notes = await world.notifications()
    pause_note = next(n for n in notes if "Scheduler PAUSADO" in n)
    assert "Origen: Telegram (telegram)" in pause_note
    summary = await world.daily_summary()
    assert "PAUSADO a proposito" in summary and "DETENIDA" in summary


async def test_resume_from_telegram_restarts_the_measurement(make_world):
    world = await world_with_an_open_position(make_world)
    await world.telegram_control("/pause")
    assert any("Scheduler PAUSADO" in n for n in await world.notifications())
    await new_signal(world)
    assert await world.cycle() == []

    replies = await world.telegram_control("/resume")

    assert "REANUDADO" in replies[1] and "la medicion sigue" in replies[1]
    candidates = await world.cycle()  # la vela pendiente se evalua ahora
    assert [(c.symbol, c.side) for c in candidates] == [("ETHUSDT", Side.SHORT)]
    assert [r["symbol"] for r in await world.arm_rows()] == ["BTCUSDT", "ETHUSDT"]
    assert (await world.get("/health")).json()["control"]["paused"] is False
    notes = await world.notifications()
    assert any("Scheduler REANUDADO" in n for n in notes)
    assert [a["action"] for a in await world.query("SELECT * FROM control_audit ORDER BY id")] == [
        "pause", "resume",
    ]


async def test_a_pause_resumed_before_the_next_poll_is_audited_but_not_announced(make_world):
    # El notificador push compara ESTADOS entre sondeos (diseno de la Fase 5): una pausa que se
    # levanta antes del siguiente sondeo no deja diferencia y no genera aviso. El rastro completo
    # esta en la auditoria. Se documenta como limite conocido (docs/FASE5_PLAN.md).
    world = await world_with_an_open_position(make_world)

    await world.telegram_control("/pause")
    await world.telegram_control("/resume")

    assert await world.notifications() == []
    audit = await world.query("SELECT action FROM control_audit ORDER BY id")
    assert [a["action"] for a in audit] == ["pause", "resume"]


async def test_telegram_ignores_a_confirmation_that_was_never_requested(make_world):
    world = await world_with_an_open_position(make_world)

    reply = await world.say("/kill SI")  # sin pedirlo antes: no hace nada

    assert "No hay una solicitud vigente" in reply and "No se hizo ningun cambio" in reply
    assert await risk_engine.is_kill_switch_active(world.db) is False
    assert await world.query("SELECT * FROM control_audit") == []


# --- dashboard -----------------------------------------------------------------------------------


async def test_kill_from_the_dashboard_needs_the_exact_word_and_stops_everything_but_the_exits(
    make_world,
):
    world = await world_with_an_open_position(make_world)
    await new_signal(world)
    before = await measured_rows(world)

    wrong = await world.control("kill", phrase="kill")  # mayusculas distintas: rechazado
    assert wrong.status_code == 422 and await risk_engine.is_kill_switch_active(world.db) is False
    assert await world.query("SELECT * FROM control_audit") == []

    ok = await world.control("kill")
    assert ok.status_code == 200
    result = ok.json()
    assert result["after"] == {"paused": True, "kill_switch": True} and result["actor"] == "admin"

    assert await world.cycle() == [] and await measured_rows(world) == before
    await world.tick("BTCUSDT", 94.0)  # el kill NO impide cerrar: el SL sigue funcionando
    assert [t["close_reason"] for t in await world.trades("CLOSED")] == ["SL"]

    status = await world.say("/status")
    assert "Kill switch: ACTIVO" in status and "Scheduler pausado: SI" in status
    health = (await world.get("/health")).json()
    assert health["control"] == {"paused": True, "kill_switch_active": True}
    audit = (await world.get("/api/control/audit")).json()["entries"]
    assert (audit[0]["channel"], audit[0]["actor"], audit[0]["action"]) == (
        "DASHBOARD", "admin", "kill",
    )
    note = next(n for n in await world.notifications() if "KILL SWITCH ACTIVADO" in n)
    assert "DETENIDA" in note and "Origen: dashboard (admin)" in note
    assert "KILL SWITCH ACTIVO" in await world.daily_summary()


async def test_resume_from_the_dashboard_lifts_a_kill_started_in_telegram(make_world):
    world = await world_with_an_open_position(make_world)
    await world.telegram_control("/kill")
    assert await risk_engine.is_kill_switch_active(world.db) is True

    resumed = await world.control("resume")

    assert resumed.status_code == 200 and resumed.json()["after"] == {
        "paused": False, "kill_switch": False,
    }
    await new_signal(world)
    assert [c.symbol for c in await world.cycle()] == ["ETHUSDT"]  # vuelve a medir y a abrir
    assert [r["outcome"] for r in await world.arm_rows()] == ["OPENED", "OPENED"]
    audit = await world.query("SELECT channel, actor, action FROM control_audit ORDER BY id")
    assert [(a["channel"], a["actor"], a["action"]) for a in audit] == [
        ("TELEGRAM", "telegram", "kill"), ("DASHBOARD", "admin", "resume"),
    ]


async def test_the_dashboard_cannot_be_driven_without_a_session_or_from_another_origin(make_world):
    world = await make_world()
    # sin sesion
    assert (await world.control("pause")).status_code == 401
    await world.login()
    # otro origen (un sitio web ajeno intentando usar la sesion del navegador)
    forged = await world.api.post(
        "/api/control/pause", json={"confirm": "PAUSAR"}, headers={"Origin": "http://evil.example"},
    )
    assert forged.status_code == 403
    assert await world.query("SELECT * FROM control_audit") == []
    assert (await world.get("/health")).json()["control"]["paused"] is False


# --- el kill switch del motor de riesgo, solo ----------------------------------------------------


async def test_the_risk_engine_kill_switch_blocks_entries_while_the_measurement_keeps_running(
    make_world,
):
    world = await make_world()
    await risk_engine.activate_kill_switch(world.db)  # sin pausar el scheduler
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})

    await world.cycle()

    shadow = (await world.shadows())[0]
    assert shadow["status"] == "OPEN" and shadow["llm_decision"] == "APROBADA"  # sigue midiendo
    row = (await world.arm_rows())[0]
    assert (row["outcome"], row["reason"]) == ("SKIPPED", "RISK_KILL_SWITCH_ACTIVE")
    assert await world.trades() == []


@pytest.mark.parametrize("command", ["/pause", "/kill"])
async def test_a_wrong_confirmation_word_never_executes(make_world, command):
    world = await make_world()
    await world.say(command)

    reply = await world.say(f"{command} SIII")

    assert "No se hizo ningun cambio" in reply
    assert await world.query("SELECT * FROM control_audit") == []
