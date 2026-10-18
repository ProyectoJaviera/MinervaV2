"""Dobles de red para correr `app.main.lifespan` REAL en proceso, sin red ni gasto: REST, WebSocket,
CoinGecko y el refresco del universo son falsos; la base es temporal. Los usan las pruebas de
cableado del brazo ejecutado (3.9) y de la recoleccion de noticias (Fase 4)."""

from __future__ import annotations

import asyncio

from app import main


class FakeRest:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def get_tickers(self, symbol=None):
        return []

    async def aclose(self) -> None:
        pass


class FakeWs:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run_forever(self) -> None:
        await asyncio.sleep(3600)

    def stop(self) -> None:
        pass

    async def set_symbols(self, symbols) -> None:
        pass


class FakeCoinGecko:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def aclose(self) -> None:
        pass


class FakeUniverse:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run_forever(self) -> None:
        await asyncio.sleep(3600)

    def stop(self) -> None:
        pass


def patch_lifespan(monkeypatch, db_path) -> None:
    """Deja `app.main.app` listo para arrancar con la red falsa y la base `db_path`."""
    monkeypatch.setattr(main, "BitunixRestClient", FakeRest)
    monkeypatch.setattr(main, "BitunixPublicWSClient", FakeWs)
    monkeypatch.setattr(main, "CoinGeckoClient", FakeCoinGecko)
    monkeypatch.setattr(main, "UniverseRefresher", FakeUniverse)
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "shutdown_logging", lambda: None)
    monkeypatch.setattr(main.settings, "database_path", str(db_path))
    monkeypatch.setattr(main.settings, "telegram_commands_enabled", False)
    monkeypatch.setattr(main.settings, "auto_open_without_llm", False)
    monkeypatch.setattr(main.settings, "llm_live_labeling_enabled", True)
    monkeypatch.setattr(main.settings, "news_collection_enabled", False)
    monkeypatch.setattr(main.settings, "lessons_memory_enabled", False)
    # `main.settings` lee el `.env` real: aunque ahi estuviera encendido, una prueba NUNCA debe
    # escribirle al chat de Telegram.
    monkeypatch.setattr(main.settings, "telegram_push_enabled", False)
