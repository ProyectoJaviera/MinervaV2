"""Fixtures de las pruebas de punta a punta: un mundo por prueba, SIEMPRE cerrado al terminar."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio

from tests.e2e.world import World

LOOPBACK_HOSTS = {None, "", b"", "localhost", "127.0.0.1", "::1", "testserver"}


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """Candado de red de TODAS las pruebas E2E: resolver cualquier nombre que no sea de este mismo
    PC (fapi.bitunix.com, api.anthropic.com, api.telegram.org, api.coingecko.com, ...) falla con un
    error claro. Asi "cero gasto y red simulada" no depende de que nadie se olvide de un doble."""
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if host not in LOOPBACK_HOSTS and not str(host).startswith("127."):
            raise OSError(f"red externa bloqueada en las pruebas E2E: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest_asyncio.fixture
async def make_world(tmp_path) -> AsyncIterator[Callable[..., Awaitable[World]]]:
    """`world = await make_world(AJUSTE=valor, ...)`. Los mundos creados se cierran al terminar la
    prueba aunque esta falle: una base de archivo abierta cuelga el proceso al salir en Windows."""
    created: list[World] = []

    async def factory(**settings: Any) -> World:
        world = await World.start(tmp_path / f"w{len(created)}", **settings)
        created.append(world)
        return world

    try:
        yield factory
    finally:
        for world in created:
            await world.stop()
