"""Aislamiento del bot de Telegram (subfase 3.7): un fallo de red, de token o una
excepcion NUNCA frena ni tumba el scheduler, el etiquetado ni el monitor de
posiciones; y el bot esta apagado por defecto (el dev server y los tests no
consumen `getUpdates`)."""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.persistence.models import TradeStatus
from app.persistence.repositories import trades_repo
from app.telegram import runtime
from app.telegram.bot import TelegramBot
from app.telegram.client import TelegramApiError
from app.telegram.commands import BotContext
from app.telegram.runtime import build_telegram_bot, maybe_start_telegram_bot, run_isolated
from app.trading.position_monitor import PositionMonitor
from tests.unit.test_position_monitor import FakeRest, _open_long, _ws_ticker, make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456:SECRET-TOKEN-ABC"


def enabled_settings(**overrides) -> Settings:
    defaults = dict(
        TELEGRAM_COMMANDS_ENABLED=True, TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="777000111",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_ctx(db) -> BotContext:
    return BotContext(
        db=db, settings=make_settings(), paper_backend=None,
        scheduler=SimpleNamespace(last_cycle_at=None), live_labeler=None,
    )


# --- apagado por defecto y construccion segura ---------------------------------------


def test_the_bot_is_off_by_default_everywhere():
    assert Settings(_env_file=None).telegram_commands_enabled is False
    assert "TELEGRAM_COMMANDS_ENABLED=false" in (REPO_ROOT / ".env.example").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        dict(TELEGRAM_COMMANDS_ENABLED=False),
        dict(TELEGRAM_BOT_TOKEN=""),
        dict(TELEGRAM_CHAT_ID=""),
    ],
)
async def test_no_bot_without_the_flag_the_token_or_the_chat_id(db, overrides):
    assert build_telegram_bot(enabled_settings(**overrides), make_ctx(db)) is None


async def test_the_bot_is_built_when_everything_is_configured(db):
    bot = build_telegram_bot(enabled_settings(), make_ctx(db))

    assert isinstance(bot, TelegramBot)
    assert bot.allowed_chat_id == "777000111"
    await bot.client.aclose()


async def test_a_negative_chat_id_warns_that_groups_are_not_accepted(db, caplog):
    with caplog.at_level("WARNING"):
        bot = build_telegram_bot(enabled_settings(TELEGRAM_CHAT_ID="-100123"), make_ctx(db))

    assert bot is not None
    assert any("solo acepta chats PRIVADOS" in r.getMessage() for r in caplog.records)
    await bot.client.aclose()


async def test_construction_failures_never_raise_and_never_log_the_token(db, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError(f"fallo con {TOKEN}")

    monkeypatch.setattr(runtime, "TelegramClient", boom)
    tasks: list[asyncio.Task] = []

    with caplog.at_level("DEBUG"):
        bot = maybe_start_telegram_bot(enabled_settings(), make_ctx(db), tasks)

    assert bot is None and tasks == []
    assert any("NO iniciado (RuntimeError)" in r.getMessage() for r in caplog.records)
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


async def test_maybe_start_registers_exactly_one_task_only_when_enabled(db):
    tasks: list[asyncio.Task] = []
    off = maybe_start_telegram_bot(enabled_settings(TELEGRAM_COMMANDS_ENABLED=False),
                                   make_ctx(db), tasks)
    assert off is None and tasks == []

    on = maybe_start_telegram_bot(enabled_settings(), make_ctx(db), tasks)
    assert on is not None and len(tasks) == 1
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]


def test_main_wires_the_bot_through_the_isolated_starter_inside_the_lifespan():
    import app.main as main

    source = inspect.getsource(main.lifespan)
    assert "maybe_start_telegram_bot(" in source
    assert "telegram_bot.stop()" in source


def test_start_bot_enables_the_commands_only_for_port_8000_and_the_dev_server_forces_them_off():
    start_bot = (REPO_ROOT / "scripts" / "start_bot.ps1").read_text(encoding="utf-8")
    dev_server = (REPO_ROOT / "scripts" / "run_dev_server.py").read_text(encoding="utf-8")

    assert 'if ($Port -eq 8000) { "true" } else { "false" }' in start_bot
    assert "$env:TELEGRAM_COMMANDS_ENABLED = $telegramCommands" in start_bot
    assert 'os.environ["TELEGRAM_COMMANDS_ENABLED"] = "false"' in dev_server
    assert 'os.environ["TELEGRAM_BOT_TOKEN"] = ""' in dev_server


# --- aislamiento --------------------------------------------------------------------------


class HostileClient:
    """Cliente que falla de TODAS las formas, sin parar."""

    def __init__(self) -> None:
        self.calls = 0
        self.errors = [
            TelegramApiError("getUpdates: HTTP 401", status_code=401),
            TelegramApiError("getUpdates: HTTP 409", status_code=409),
            TelegramApiError("getUpdates: HTTP 429", status_code=429),
            TelegramApiError("getUpdates: ConnectError"),
            TelegramApiError("getUpdates: respuesta no JSON"),
            KeyError("update_id"),
            RuntimeError("excepcion inesperada"),
            TimeoutError(),
        ]

    async def get_updates(self, *, offset, timeout, limit=100):
        self.calls += 1
        raise self.errors[self.calls % len(self.errors)]

    async def send_message(self, chat_id, text):  # pragma: no cover - nunca llega a responder
        raise TelegramApiError("sendMessage: ConnectError")

    async def aclose(self):
        return None


class CountingLabeler:
    def __init__(self) -> None:
        self.cycle_starts = 0

    def note_cycle_start(self) -> None:
        self.cycle_starts += 1

    def maybe_label(self, trade) -> None:  # pragma: no cover - sin candidatos
        raise AssertionError("no deberia etiquetar")


async def test_a_hostile_telegram_never_slows_the_scheduler_the_labeler_or_the_monitor(
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

    client = HostileClient()

    async def tiny_sleep(seconds):
        await asyncio.sleep(0.001)

    bot = TelegramBot(
        client=client, allowed_chat_id="777000111", handler=lambda text: None, sleep=tiny_sleep,
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
        asyncio.create_task(run_isolated(bot)),
        asyncio.create_task(ticker()),
    ]
    await asyncio.sleep(0.15)
    # Con Telegram fallando sin parar, el monitor sigue cerrando posiciones por tick.
    await monitor.handle_ws_message(_ws_ticker("BTCUSDT", 94.5))
    await asyncio.sleep(0.35)
    scheduler.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED and closed.close_reason == "SL"
    assert client.calls > 20  # el bot estuvo fallando todo el tiempo...
    assert generation["cycles"] >= 5 and labeler.cycle_starts >= 5  # ...y el scheduler avanzo
    assert scheduler.last_cycle_at is not None
    assert max(gaps) < 0.25  # el event loop nunca se bloqueo


async def test_a_bot_that_dies_with_a_bug_does_not_propagate_to_anyone(db, caplog):
    class Exploding:
        async def run_forever(self):
            raise RuntimeError(f"bug interno con {TOKEN}")

    with caplog.at_level("ERROR"):
        await asyncio.wait_for(run_isolated(Exploding()), timeout=1)  # no lanza

    assert any("termino de forma inesperada (RuntimeError)" in r.getMessage()
               for r in caplog.records)
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


async def test_cancelling_the_isolated_bot_still_propagates_cancellation(db):
    class Idle:
        async def run_forever(self):
            await asyncio.sleep(3600)

    task = asyncio.create_task(run_isolated(Idle()))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
