"""/pause, /resume, /kill con confirmacion de dos pasos (subfase 3.7). Pausar DETIENE
la medicion: la advertencia lo dice, nada cambia sin un `SI` vigente, y las acciones
quedan registradas."""

from __future__ import annotations

import pytest

from app.persistence.repositories import system_state_repo
from app.telegram.commands import CommandRouter
from app.telegram.confirmation import CONFIRM_WINDOW_SECONDS, ConfirmationManager, is_yes
from app.trading import control_state, risk_engine
from tests.unit.test_telegram_commands import make_ctx


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_router(db):
    ctx, _ = make_ctx(db)
    clock = Clock()
    router = CommandRouter(ctx, ConfirmationManager(now=clock))
    return router, clock


async def state(db) -> tuple[bool, bool]:
    return (
        await control_state.is_scheduler_paused(db),
        await risk_engine.is_kill_switch_active(db),
    )


# --- ConfirmationManager ----------------------------------------------------------------


def test_is_yes_accepts_only_a_single_si_in_any_case():
    assert is_yes(["SI"]) and is_yes(["si"]) and is_yes(["Sí"])
    assert not is_yes([]) and not is_yes(["NO"]) and not is_yes(["SI", "SI"])
    assert not is_yes(["siguiente"])


def test_confirmation_window_is_sixty_seconds_and_a_request_is_consumed_once():
    clock = Clock()
    manager = ConfirmationManager(now=clock)
    assert CONFIRM_WINDOW_SECONDS == 60.0

    manager.request("/pause")
    clock.t += 59
    assert manager.confirm("/pause") is True
    assert manager.confirm("/pause") is False  # consumida: un SI repetido no ejecuta de nuevo


def test_confirmation_expires_after_the_window():
    clock = Clock()
    manager = ConfirmationManager(now=clock)

    manager.request("/kill")
    clock.t += 60.01

    assert manager.confirm("/kill") is False


def test_confirmation_is_scoped_to_the_same_command_and_one_request_at_a_time():
    clock = Clock()
    manager = ConfirmationManager(now=clock)

    manager.request("/pause")
    assert manager.confirm("/kill") is False  # SI de otro comando no sirve
    manager.request("/kill")  # pedir otro comando reemplaza la solicitud anterior
    assert manager.confirm("/pause") is False
    assert manager.confirm("/kill") is True
    assert manager.confirm("/resume") is False  # nunca se pidio


# --- flujo por el enrutador -------------------------------------------------------------------


async def test_pause_without_confirmation_warns_and_changes_nothing(db):
    router, _ = make_router(db)

    reply = await router("/pause")

    assert reply.startswith("MODO PAPER TRADING")
    assert "DETIENE LA MEDICION" in reply
    assert "/pause SI" in reply and "60 s" in reply
    assert await state(db) == (False, False)


async def test_pause_then_si_within_the_window_pauses_the_scheduler(db):
    router, clock = make_router(db)
    await router("/pause")
    clock.t += 30

    reply = await router("/pause SI")

    assert "Scheduler PAUSADO" in reply and "medicion esta DETENIDA" in reply
    assert await state(db) == (True, False)  # /pause no activa el kill switch


async def test_si_after_the_window_does_not_execute(db):
    router, clock = make_router(db)
    await router("/pause")
    clock.t += 61

    reply = await router("/pause SI")

    assert "No hay una solicitud vigente de /pause" in reply
    assert "No se hizo ningun cambio" in reply
    assert await state(db) == (False, False)


async def test_si_without_a_prior_request_never_executes(db):
    router, _ = make_router(db)

    for command in ("/pause SI", "/kill SI", "/resume SI"):
        assert "No hay una solicitud vigente" in await router(command)

    assert await state(db) == (False, False)


async def test_si_for_a_different_command_does_not_execute(db):
    router, _ = make_router(db)
    await router("/pause")

    reply = await router("/kill SI")

    assert "No hay una solicitud vigente de /kill" in reply
    assert await state(db) == (False, False)


async def test_a_repeated_si_does_not_execute_twice(db):
    router, _ = make_router(db)
    await router("/pause")
    await router("/pause SI")
    await control_state.set_scheduler_paused(db, False)  # alguien reanuda

    second = await router("/pause SI")

    assert "No hay una solicitud vigente" in second
    assert await state(db) == (False, False)


async def test_kill_activates_the_kill_switch_and_pauses_the_scheduler(db):
    router, _ = make_router(db)
    warning = await router("/kill")
    assert "kill switch" in warning and "DETIENE LA MEDICION" in warning
    assert await state(db) == (False, False)

    reply = await router("/kill SI")

    assert "KILL SWITCH ACTIVO" in reply and "scheduler PAUSADO" in reply
    assert await state(db) == (True, True)


async def test_resume_lifts_both_the_pause_and_the_kill_switch(db):
    router, _ = make_router(db)
    await router("/kill")
    await router("/kill SI")
    assert await state(db) == (True, True)
    warning = await router("/resume")
    assert "/resume SI" in warning
    assert await state(db) == (True, True)  # sin confirmar no cambia nada

    reply = await router("/resume SI")

    assert "Scheduler REANUDADO" in reply and "kill switch DESACTIVADO" in reply
    assert await state(db) == (False, False)


async def test_resume_warns_when_the_drawdown_stop_is_still_active(db):
    router, _ = make_router(db)
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")
    await router("/resume")

    reply = await router("/resume SI")

    assert "stop por drawdown sigue ACTIVO" in reply and "no por Telegram" in reply
    assert await risk_engine.is_drawdown_stop_active(db) is True  # no se toca


async def test_unexpected_arguments_are_rejected_without_changes(db):
    router, _ = make_router(db)
    await router("/pause")

    for text in ("/pause NO", "/pause SI SI", "/pause ahora"):
        assert "Uso: /pause" in await router(text)

    assert await state(db) == (False, False)


async def test_executed_actions_leave_an_audit_trail(db, caplog):
    router, _ = make_router(db)
    with caplog.at_level("WARNING"):
        await router("/pause")
        await router("/pause SI")

    stamp = await system_state_repo.get_state(db, "telegram_last_control_action")
    assert stamp and stamp.startswith("/pause 2026-10-17T15:30")
    assert any("accion de control ejecutada: /pause" in r.getMessage() for r in caplog.records)


async def test_the_command_suffix_and_case_do_not_bypass_the_confirmation(db):
    router, _ = make_router(db)

    await router("/PAUSE@MinervaBot")
    assert await state(db) == (False, False)
    await router("/Pause@MinervaBot si")

    assert await state(db) == (True, False)


@pytest.mark.parametrize("command", ["/pause", "/resume", "/kill"])
async def test_every_control_command_asks_for_confirmation_first(db, command):
    router, _ = make_router(db)

    reply = await router(command)

    assert f"{command} SI" in reply
    assert await state(db) == (False, False)
