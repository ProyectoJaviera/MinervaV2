"""Fase 4, aislamiento de la memoria de lecciones (mismas garantias que el colector de noticias, la
alerta de Telegram y el brazo ejecutado): apagada por defecto, corre en su propia tarea, un ejecutor
hostil (que falla sin parar o se cuelga) no frena al scheduler, al etiquetado ni al monitor, y el
paquete `app.memory` no conoce el nucleo de la medicion ni la API de Anthropic. Sin red ni gasto."""

from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.memory.runtime import LessonsRunner
from app.persistence.models import TradeStatus
from app.persistence.repositories import trades_repo
from app.trading.position_monitor import PositionMonitor
from tests.support.winproc import NO_WINDOW
from tests.unit.lifespan_fakes import patch_lifespan
from tests.unit.test_news_runtime_isolation import (
    FORBIDDEN_FOR_NEWS,
    CountingLabeler,
    _imports,
)
from tests.unit.test_position_monitor import FakeRest, _open_long, _ws_ticker, make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = REPO_ROOT / "app" / "memory"


def lessons_settings(**overrides) -> Settings:
    return Settings(_env_file=None, LESSONS_MEMORY_ENABLED=True, **overrides)  # type: ignore[call-arg]


# --- un ejecutor hostil no frena a nadie ---------------------------------------------------------


class HostileRunner(LessonsRunner):
    """`run_once` falla de todas las formas; una de cada dos veces se cuelga."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.calls = 0
        self.hung = asyncio.Event()

    async def run_once(self, now=None) -> dict:
        self.calls += 1
        if self.calls % 2 == 0:
            await self.hung.wait()  # se cuelga hasta que el test lo cancele
        raise [RuntimeError("bug"), ValueError("basura"), OSError("disco")][self.calls % 3]


async def test_a_hostile_runner_never_slows_the_scheduler_the_labeler_or_the_monitor(
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
    runner = HostileRunner(db, lessons_settings(LESSONS_REBUILD_HOURS=0.0000015))
    gaps: list[float] = []

    async def ticker():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    async def isolated_fast():
        await run_isolated_with(runner, startup_delay_s=0.0)

    tasks = [
        asyncio.create_task(scheduler.run_forever()),
        asyncio.create_task(isolated_fast()),
        asyncio.create_task(ticker()),
    ]
    await asyncio.sleep(0.15)
    await monitor.handle_ws_message(_ws_ticker("BTCUSDT", 94.5))
    await asyncio.sleep(0.35)
    scheduler.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED and closed.close_reason == "SL"
    assert runner.calls >= 2  # el ejecutor estuvo fallando (y colgandose) todo el rato...
    assert generation["cycles"] >= 5 and labeler.cycle_starts >= 5  # ...y el scheduler avanzo
    assert scheduler.last_cycle_at is not None
    assert max(gaps) < 0.25  # el event loop nunca se bloqueo


async def run_isolated_with(runner: LessonsRunner, *, startup_delay_s: float) -> None:
    """`run_isolated` sin el retraso de arranque de 5 minutos."""
    try:
        await runner.run_forever(startup_delay_s=startup_delay_s)
    except asyncio.CancelledError:
        raise


async def test_a_runner_blocked_in_the_database_does_not_hold_other_tasks(db, monkeypatch):
    """Aunque su unica consulta no termine nunca, el resto del sistema sigue con su vida."""
    blocked = asyncio.Event()

    async def never_returns(*args, **kwargs):
        await blocked.wait()

    monkeypatch.setattr("app.memory.runtime.refresh_entries", never_returns)
    runner = LessonsRunner(db, lessons_settings())
    task = asyncio.create_task(run_isolated_with(runner, startup_delay_s=0.0))

    await asyncio.sleep(0.05)
    other = await asyncio.wait_for(db.fetch_one("SELECT 1 AS one"), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert other["one"] == 1 and runner.last_run_at is None


# --- cableado en el lifespan real ----------------------------------------------------------------


@pytest.fixture
def bot(tmp_path, monkeypatch):
    patch_lifespan(monkeypatch, tmp_path / "lifespan.db")


def test_with_the_switch_off_there_is_no_runner(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "lessons_memory_enabled", False)

    with TestClient(main.app) as client:
        assert main.app.state.lessons_runner is None
        assert client.get("/health").json()["llm_labeling"]["frozen_hash_ok"] is True


def test_with_the_switch_on_the_runner_is_wired_and_the_measurement_is_untouched(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "lessons_memory_enabled", True)

    with TestClient(main.app) as client:
        runner = main.app.state.lessons_runner
        health = client.get("/health").json()

        assert runner is not None
        assert health["llm_labeling"]["frozen_hash_ok"] is True
        assert main.app.state.scheduler is not None and main.app.state.monitor is not None
        assert main.app.state.live_labeler is not None

    assert runner._stop.is_set()  # se detuvo al cerrar


# --- estructura: el paquete no conoce el nucleo --------------------------------------------------


ALLOWED_APP_IMPORTS = (
    "app.config", "app.core.logging", "app.memory", "app.persistence",
    # la unica pieza de la medicion que lee: la puerta de los resultados (solo lectura)
    "app.trading.measurement_status",
    # funcion pura de cadenas (`BTCUSDT` -> `BTC`); no arrastra el cliente HTTP de noticias
    "app.news.assets",
)


def test_the_memory_package_never_imports_the_core_nor_the_anthropic_api():
    forbidden = (*FORBIDDEN_FOR_NEWS, "httpx", "requests", "aiohttp", "app.news.fetch",
                 "app.news.collector", "app.news.runtime")
    offenders = []
    for path in MEMORY_DIR.rglob("*.py"):
        for name in _imports(path):
            if any(name == bad or name.startswith(bad + ".") for bad in forbidden):
                offenders.append(f"{path.name}: {name}")

    assert offenders == []


def test_every_app_module_the_memory_package_uses_is_on_the_allow_list():
    """Una dependencia nueva hacia el resto de `app` debe ser una decision consciente."""
    unexpected = set()
    for path in MEMORY_DIR.rglob("*.py"):
        for name in _imports(path):
            if name.startswith("app.") and not name.startswith(ALLOWED_APP_IMPORTS):
                unexpected.add(f"{path.name}: {name}")

    assert unexpected == set()


def _loaded_after_importing(modules: list[str], prefixes: tuple[str, ...]) -> list[str]:
    code = (
        "import sys, importlib; sys.path.insert(0, '.')\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(sorted(m for m in sys.modules if m.startswith({prefixes!r})))"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=120, creationflags=NO_WINDOW,
    )
    assert proc.returncode == 0, proc.stderr
    return ast.literal_eval(proc.stdout.strip())


def _memory_modules(*, except_names: tuple[str, ...] = ()) -> list[str]:
    return [
        f"app.memory.{p.stem}" for p in sorted(MEMORY_DIR.glob("*.py"))
        if p.stem != "__init__" and p.stem not in except_names
    ]


def test_what_runs_inside_the_bot_loads_no_llm_core_backend_or_network_code():
    """Todo el paquete salvo `gate` (que solo lee la puerta de resultados): ni el LLM, ni el
    etiquetador, ni el scheduler, ni un backend, ni un cliente HTTP, ni la red de noticias."""
    forbidden = (
        "anthropic", "httpx", "app.llm", "app.trading", "app.execution", "app.core.scheduler",
        "app.news.fetch", "app.news.collector", "app.news.runtime",
    )

    assert _loaded_after_importing(_memory_modules(except_names=("gate",)), forbidden) == []


def test_even_the_gate_never_loads_an_anthropic_client_a_backend_or_the_trading_core():
    """`gate` importa `measurement_status`, que lee el hash congelado y por eso carga los MODULOS
    del LLM y del etiquetador (como cualquier script de estado); eso es solo lectura. Lo que jamas
    debe cargarse: Anthropic, un backend de ejecucion o el nucleo que opera."""
    forbidden = (
        "anthropic", "app.execution", "app.core.scheduler", "app.trading.position_monitor",
        "app.trading.risk_engine", "app.trading.shadow_book", "app.trading.signal_generator",
        "app.trading.paper_execution", "app.news.fetch", "app.news.collector",
        "app.news.runtime",
    )

    assert _loaded_after_importing(_memory_modules(), forbidden) == []


def test_the_memory_package_only_writes_its_own_tables():
    """Todas las sentencias de escritura del paquete y de su repositorio apuntan a tablas de la
    memoria: nunca a `shadow_trades`, `llm_logs`, `trades` ni a la medicion."""
    import re

    own = {"memory_entries", "lessons", "lesson_applications", "lessons_reports"}
    sources = [*MEMORY_DIR.rglob("*.py"),
               REPO_ROOT / "app" / "persistence" / "repositories" / "memory_repo.py"]
    writes: set[str] = set()
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)", text,
            re.IGNORECASE,
        ):
            writes.add(match.group(1).lower())

    assert writes and writes <= own, writes - own
