"""Fase 4, aislamiento de la recoleccion de noticias (como se hizo con Telegram y con el brazo
ejecutado): apagada por defecto, construirla no puede lanzar, corre en su propia tarea, un colector
hostil (que falla sin parar o se cuelga) no frena al scheduler, al etiquetado ni al monitor, y el
paquete `app.news` no conoce nada del nucleo ni de la API de Anthropic. Sin red ni gasto."""

from __future__ import annotations

import ast
import asyncio
import logging
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.news import runtime
from app.news.collector import NewsCollector
from app.news.fetch import HttpFetcher
from app.news.runtime import build_news_collector, maybe_start_news_collector, run_isolated
from app.persistence.models import TradeStatus
from app.persistence.repositories import trades_repo
from app.trading.position_monitor import PositionMonitor
from tests.unit.lifespan_fakes import patch_lifespan
from tests.unit.test_position_monitor import FakeRest, _open_long, _ws_ticker, make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def news_settings(**overrides) -> Settings:
    return Settings(_env_file=None, NEWS_COLLECTION_ENABLED=True, **overrides)  # type: ignore[call-arg]


def forbidden_transport() -> httpx.MockTransport:
    def handler(request):
        raise AssertionError(f"el test no debe tocar la red: {request.url}")

    return httpx.MockTransport(handler)


# --- construccion y arranque ------------------------------------------------------------------


def test_it_is_not_even_built_when_the_switch_is_off(db):
    assert build_news_collector(Settings(_env_file=None), db) is None  # type: ignore[call-arg]


async def test_building_it_never_raises_even_if_something_inside_breaks(db, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("fallo al construir el cliente HTTP con token=SECRETO")

    monkeypatch.setattr(runtime, "HttpFetcher", broken)

    with caplog.at_level(logging.ERROR, logger="app.news.runtime"):
        assert build_news_collector(news_settings(), db) is None

    assert any("NO iniciada (RuntimeError)" in r.getMessage() for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


async def test_maybe_start_registers_exactly_one_task_only_when_enabled(db):
    tasks: list[asyncio.Task] = []

    assert maybe_start_news_collector(Settings(_env_file=None), db, tasks) is None  # type: ignore[call-arg]
    assert tasks == []

    collector = maybe_start_news_collector(news_settings(), db, tasks)
    assert collector is not None and len(tasks) == 1
    collector.stop()
    await asyncio.wait_for(tasks[0], timeout=2)  # se detiene sola durante el retraso de arranque
    await collector.aclose()


async def test_a_collector_that_dies_with_a_bug_does_not_propagate_to_anyone(caplog):
    class Exploding:
        async def run_forever(self):
            raise RuntimeError("bug interno con clave=SECRETO")

    with caplog.at_level(logging.ERROR, logger="app.news.runtime"):
        await asyncio.wait_for(run_isolated(Exploding()), timeout=1)  # no lanza

    assert any("termino de forma inesperada (RuntimeError)" in r.getMessage()
               for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


# --- un colector hostil no frena a nadie --------------------------------------------------------


class HostileFetcher:
    """Falla de todas las formas posibles, sin parar; una de cada tres veces se cuelga."""

    def __init__(self) -> None:
        self.calls = 0
        self.hung = asyncio.Event()

    async def get(self, url, **kwargs):
        self.calls += 1
        if self.calls % 3 == 0:
            await self.hung.wait()  # se cuelga hasta que el test lo cancele
        raise [
            httpx.ConnectError("sin red"), RuntimeError("bug"), TimeoutError(),
            ValueError("basura"),
        ][self.calls % 4]

    async def aclose(self):
        return None


class CountingLabeler:
    def __init__(self) -> None:
        self.cycle_starts = 0

    def note_cycle_start(self) -> None:
        self.cycle_starts += 1

    def maybe_label(self, trade, candidate=None) -> None:  # pragma: no cover - sin candidatos
        raise AssertionError("no deberia etiquetar")


async def test_a_hostile_collector_never_slows_the_scheduler_the_labeler_or_the_monitor(
    db, monkeypatch,
):
    generation = {"cycles": 0}

    async def fake_generation(db_, rest_, settings_):
        generation["cycles"] += 1
        return []

    monkeypatch.setattr("app.core.scheduler.run_signal_generation_cycle", fake_generation)
    settings = make_settings(SCHEDULER_POLL_SECONDS=0)
    rest = FakeRest(last=100.0)
    backend = PaperBackend(db, rest, settings)
    labeler = CountingLabeler()
    scheduler = Scheduler(db, rest, backend, settings, live_labeler=labeler)
    monitor = PositionMonitor(db, rest, backend, settings)
    trade = await _open_long(backend)
    await monitor._reload_trades()

    fetcher = HostileFetcher()

    async def tiny_sleep(seconds):
        await asyncio.sleep(0.001)

    collector = NewsCollector(
        db, news_settings(), fetcher, sleep=tiny_sleep, symbols_provider=lambda: _symbols(),
    )
    gaps: list[float] = []

    async def ticker():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    tasks = [
        asyncio.create_task(scheduler.run_forever()),
        asyncio.create_task(run_isolated_fast(collector)),
        asyncio.create_task(ticker()),
    ]
    await asyncio.sleep(0.15)
    # Con el colector fallando y colgandose, el monitor sigue cerrando posiciones por tick.
    await monitor.handle_ws_message(_ws_ticker("BTCUSDT", 94.5))
    await asyncio.sleep(0.35)
    scheduler.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED and closed.close_reason == "SL"
    assert fetcher.calls >= 3  # el colector estuvo fallando (y colgandose) todo el rato...
    assert generation["cycles"] >= 5 and labeler.cycle_starts >= 5  # ...y el scheduler avanzo
    assert scheduler.last_cycle_at is not None
    assert max(gaps) < 0.25  # el event loop nunca se bloqueo


async def _symbols() -> list[str]:
    return ["BTCUSDT"]


async def run_isolated_fast(collector: NewsCollector) -> None:
    """`run_isolated` sin el retraso de arranque de 60 s ni la espera de 60 s entre ciclos."""
    try:
        await collector.run_forever(tick_seconds=0.01, startup_delay_s=0.0)
    except asyncio.CancelledError:
        raise


# --- cableado en el lifespan real ---------------------------------------------------------------


@pytest.fixture
def bot(tmp_path, monkeypatch):
    patch_lifespan(monkeypatch, tmp_path / "lifespan.db")
    # Aunque el colector arranque, jamas toca la red: su cliente HTTP es uno sin red.
    real = HttpFetcher

    def offline(**kwargs):
        kwargs["transport"] = forbidden_transport()
        return real(**kwargs)

    monkeypatch.setattr(runtime, "HttpFetcher", offline)


def test_with_the_switch_on_the_collector_is_wired_isolated_and_reported_in_health(
    bot, monkeypatch,
):
    monkeypatch.setattr(main.settings, "news_collection_enabled", True)

    with TestClient(main.app) as client:
        collector = main.app.state.news_collector
        health = client.get("/health").json()

        assert collector is not None
        assert health["news"]["enabled"] is True and health["news"]["active"] is True
        assert set(health["news"]["sources"]) == {"gdelt", "fed_monetary", "fear_greed"}
        assert health["news"]["items_24h"] == 0
        assert len(health["news"]["attribution"]) == 3
        assert health["status"] in {"ok", "stale"}
        # el resto del bot no depende del colector
        assert health["llm_labeling"]["frozen_hash_ok"] is True
        assert main.app.state.scheduler is not None and main.app.state.monitor is not None


def test_with_the_switch_off_there_is_no_collector_and_health_says_so(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "news_collection_enabled", False)

    with TestClient(main.app) as client:
        assert main.app.state.news_collector is None
        news = client.get("/health").json()["news"]

    assert news["enabled"] is False and news["active"] is False
    assert news["sources"] == {} and news["items_24h"] == 0


# --- estructura: el paquete no conoce el nucleo y el nucleo no conoce el paquete ----------------


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


FORBIDDEN_FOR_NEWS = (
    "anthropic", "app.llm", "app.core.scheduler", "app.trading.shadow_live_labeler",
    "app.trading.paper_execution", "app.trading.position_monitor", "app.execution",
    "app.trading.risk_engine", "app.trading.shadow_book", "app.trading.signal_generator",
)


def test_the_news_package_never_imports_the_core_nor_the_anthropic_api():
    offenders = []
    for path in (REPO_ROOT / "app" / "news").rglob("*.py"):
        for name in _imports(path):
            if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN_FOR_NEWS):
                offenders.append(f"{path.name}: {name}")

    assert offenders == []


def test_nothing_in_the_core_imports_the_news_the_memory_or_the_v5_prompt():
    allowed = {
        "main.py", "deps.py", "health.py", "news.py", "memory.py", "prompts_v5.py",
    }
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        parts = path.relative_to(REPO_ROOT / "app").parts
        if parts[0] in {"news", "memory"} or path.name in allowed:
            continue
        for name in _imports(path):
            if name in {"app.news", "app.memory"} or name.startswith(
                ("app.news.", "app.memory.", "app.llm.prompts_v5")
            ):
                offenders.append(f"{'/'.join(parts)}: {name}")

    assert offenders == []


def test_the_files_allowed_to_touch_the_news_are_the_wiring_and_read_only_routes():
    """Quien SI importa `app.news`/`app.memory`: solo el cableado (main.py), la API de solo
    lectura y `/health`; nunca el prompt congelado, el labeler, el scheduler, el brazo ni el
    monitor."""
    importers = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        parts = path.relative_to(REPO_ROOT / "app").parts
        if parts[0] in {"news", "memory"}:
            continue
        if any(n.startswith(("app.news", "app.memory", "app.llm.prompts_v5"))
               for n in _imports(path)):
            importers.add("/".join(parts))

    frozen_path = {
        "llm/prompts.py", "llm/market_features.py", "llm/decision_service.py",
        "trading/shadow_live_labeler.py", "core/scheduler.py", "trading/paper_execution.py",
        "trading/position_monitor.py", "trading/risk_engine.py",
    }
    assert not importers & frozen_path
