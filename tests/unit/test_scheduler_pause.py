"""Pausa persistente del scheduler (subfase 3.7): pausar DETIENE la medicion
(sin senales, sombras ni etiquetado) pero NUNCA frena los cierres de posiciones
ni el motor de riesgo; el estado sobrevive a un reinicio y se ve en /health."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.routes.health import health
from app.config import Settings
from app.core.heartbeat import Heartbeat
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.llm.decision_service import LlmDecisionService
from app.persistence.database import Database
from app.persistence.models import TradeStatus
from app.persistence.repositories import trades_repo
from app.trading import control_state, risk_engine
from app.trading.position_monitor import PositionMonitor
from app.trading.shadow_live_labeler import ShadowLiveLabeler
from tests.unit.test_position_monitor import FakeRest, _open_long, _ws_ticker, make_settings


class SpyLabeler:
    def __init__(self) -> None:
        self.cycle_starts = 0

    def note_cycle_start(self) -> None:
        self.cycle_starts += 1

    def maybe_label(self, trade) -> None:  # pragma: no cover - sin candidatos
        raise AssertionError("no deberia etiquetar")


def _scheduler(db, monkeypatch, labeler=None):
    calls = {"generation": 0}

    async def fake_generation(db_, rest_, settings_):
        calls["generation"] += 1
        return []

    monkeypatch.setattr("app.core.scheduler.run_signal_generation_cycle", fake_generation)
    settings = make_settings()
    rest = FakeRest(last=100.0)
    backend = PaperBackend(db, rest, settings)
    scheduler = Scheduler(db, rest, backend, settings, live_labeler=labeler)
    return scheduler, calls


async def test_paused_scheduler_generates_nothing_and_does_not_advance_last_cycle(
    db, monkeypatch,
):
    labeler = SpyLabeler()
    scheduler, calls = _scheduler(db, monkeypatch, labeler)
    await control_state.set_scheduler_paused(db, True)

    result = await scheduler.run_cycle()

    assert result == []
    assert calls["generation"] == 0
    assert labeler.cycle_starts == 0
    assert scheduler.last_cycle_at is None


async def test_resume_restores_the_normal_cycle(db, monkeypatch):
    labeler = SpyLabeler()
    scheduler, calls = _scheduler(db, monkeypatch, labeler)
    await control_state.set_scheduler_paused(db, True)
    await scheduler.run_cycle()

    await control_state.set_scheduler_paused(db, False)
    await scheduler.run_cycle()

    assert calls["generation"] == 1 and labeler.cycle_starts == 1
    assert isinstance(scheduler.last_cycle_at, datetime)


async def test_pause_warning_is_logged_once_per_pause_streak(db, monkeypatch, caplog):
    scheduler, _calls = _scheduler(db, monkeypatch)
    await control_state.set_scheduler_paused(db, True)

    with caplog.at_level("WARNING"):
        await scheduler.run_cycle()
        await scheduler.run_cycle()
        await scheduler.run_cycle()

    assert sum("Scheduler PAUSADO" in r.getMessage() for r in caplog.records) == 1


async def test_pause_never_stops_position_closures(db):
    rest = FakeRest(last=100.0)
    settings = make_settings()
    backend = PaperBackend(db, rest, settings)
    monitor = PositionMonitor(db, rest, backend, settings)
    trade = await _open_long(backend)
    await monitor._reload_trades()
    await control_state.set_scheduler_paused(db, True)
    await risk_engine.activate_kill_switch(db)

    await monitor.handle_ws_message(_ws_ticker("BTCUSDT", 94.5))  # cruza el SL de 95

    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED and closed.close_reason == "SL"


async def test_pause_and_kill_switch_survive_a_restart(tmp_path):
    path = str(tmp_path / "restart.db")
    first = Database(path)
    await first.connect()
    await control_state.set_scheduler_paused(first, True)
    await risk_engine.activate_kill_switch(first)
    await first.close()

    second = Database(path)
    await second.connect()
    try:
        assert await control_state.is_scheduler_paused(second) is True
        assert await risk_engine.is_kill_switch_active(second) is True
        await control_state.set_scheduler_paused(second, False)
        await risk_engine.deactivate_kill_switch(second)
        assert await control_state.is_scheduler_paused(second) is False
        assert await risk_engine.is_kill_switch_active(second) is False
    finally:
        await second.close()


class _FakeSchedulerForHealth:
    def __init__(self, last_cycle_at) -> None:
        self.last_cycle_at = last_cycle_at


async def test_health_reports_the_control_block(db):
    settings = Settings(_env_file=None)
    labeler = ShadowLiveLabeler(db, settings, LlmDecisionService(db, settings))
    heartbeat = Heartbeat()
    heartbeat.beat()
    scheduler = _FakeSchedulerForHealth(datetime.now(UTC))

    normal = await health(heartbeat=heartbeat, scheduler=scheduler, live_labeler=labeler, db=db)
    await control_state.set_scheduler_paused(db, True)
    await risk_engine.activate_kill_switch(db)
    paused = await health(heartbeat=heartbeat, scheduler=scheduler, live_labeler=labeler, db=db)

    assert normal["control"] == {"paused": False, "kill_switch_active": False}
    assert paused["control"] == {"paused": True, "kill_switch_active": True}
    assert paused["status"] == "ok"  # el ciclo era reciente; la bandera es la que avisa


@pytest.mark.parametrize("value, expected", [(None, False), ("0", False), ("1", True)])
async def test_unknown_or_cleared_state_means_not_paused(db, value, expected):
    if value is not None:
        from app.persistence.repositories import system_state_repo

        await system_state_repo.set_state(db, control_state.KEY_SCHEDULER_PAUSED, value)

    assert await control_state.is_scheduler_paused(db) is expected
