"""E2E: las pruebas de punta a punta no gastan ni tocan el exterior. El candado de red de
`conftest.py` bloquea cualquier nombre que no sea local; el cliente de Anthropic, Bitunix y
Telegram son dobles; el `.env` real no se lee; y el sistema bajo prueba es el REAL (no un doble
de si mismo)."""

from __future__ import annotations

import socket

import pytest

from app.persistence.models import Side
from app.trading.paper_execution import PaperExecutionArm
from app.trading.shadow_live_labeler import ShadowLiveLabeler
from tests.e2e.world import Market, ScriptedLlm, ScriptedRest


@pytest.mark.parametrize("host", [
    "api.anthropic.com", "fapi.bitunix.com", "api.telegram.org", "api.coingecko.com",
    "data-api.alternative.me",
])
def test_the_network_guard_blocks_every_external_service_the_bot_talks_to(host):
    with pytest.raises(OSError, match="red externa bloqueada"):
        socket.getaddrinfo(host, 443)


def test_the_network_guard_lets_this_machine_through():
    assert socket.getaddrinfo("127.0.0.1", 8000)


async def test_a_full_scenario_touches_no_external_service_and_spends_nothing(make_world):
    world = await make_world()
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})

    await world.cycle()
    await world.sync()
    await world.tick("BTCUSDT", 110.1)

    # El cliente de Anthropic es el doble programable: la fabrica real nunca se invoco.
    assert world.labeler.decision_service._client is world.llm
    assert isinstance(world.llm, ScriptedLlm) and len(world.llm.calls) == 1
    # Bitunix: solo el doble, y unicamente con las llamadas de datos publicos previstas.
    assert isinstance(world.rest, ScriptedRest)
    assert set(world.rest.calls) <= {"get_tickers", "get_funding_rate_history", "get_kline"}
    # Credenciales: nada real en los ajustes del escenario.
    settings = world.settings
    assert settings.anthropic_api_key == "" and settings.telegram_bot_token == "0:E2E-FAKE"
    assert settings.coingecko_api_key == ""


async def test_the_system_under_test_is_the_real_one_not_a_copy_of_it(make_world):
    from app import main

    world = await make_world()

    assert isinstance(world.arm, PaperExecutionArm)
    assert isinstance(world.labeler, ShadowLiveLabeler)
    assert world.labeler.on_decision == world.arm.submit  # el mismo cableado que el lifespan real
    assert main.app.state.live_labeler is world.labeler and main.app.state.db is world.db
    paths = set(main.app.openapi()["paths"])  # (`app.routes` guarda routers, no rutas planas)
    assert {"/health", "/trades", "/positions", "/api/account", "/api/control/{action}"} <= paths
