"""Subfase 3.9, humo del cableado en `app.main.lifespan`: el brazo se construye, se engancha al
labeler (`on_decision`), se activa solo con el interruptor, publica su bloque en `/health` y se
cierra ordenadamente. El `lifespan` REAL corre en proceso; solo las conexiones de red (REST,
WebSocket, CoinGecko, universo) son falsas, asi que no hay red ni gasto."""

from __future__ import annotations

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main
from tests.unit.lifespan_fakes import patch_lifespan


@pytest.fixture
def bot(tmp_path, monkeypatch):
    """El `app.main.app` real, con la red falsa y una base temporal."""
    db_path = tmp_path / "lifespan.db"
    patch_lifespan(monkeypatch, db_path)
    return db_path


def _system_state(db_path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return dict(conn.execute("SELECT key, value FROM system_state"))
    finally:
        conn.close()


def test_with_the_switch_on_the_arm_is_wired_active_and_reported_in_health(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "paper_execution_enabled", True)

    with TestClient(main.app) as client:
        arm = main.app.state.paper_execution
        labeler = main.app.state.live_labeler
        health = client.get("/health").json()

        assert arm.active is True
        assert labeler.on_decision == arm.submit  # enganchado al etiquetado
        assert arm.paper_backend is main.app.state.paper_backend
        assert health["paper_execution"]["enabled"] is True
        assert health["paper_execution"]["active"] is True
        assert health["paper_execution"]["armed_since"] is not None
        assert health["paper_execution"]["opened_today"] == 0
        assert health["llm_labeling"]["frozen_hash_ok"] is True  # el etiquetado sigue intacto
        assert "pnl" not in str(health["paper_execution"]).lower()

    state = _system_state(bot)
    assert "real_arm_started_at" in state and state["real_arm_drawdown_baseline"] == "0"
    assert arm._tasks == set()  # el cierre ordenado dejo todo limpio


def test_with_the_switch_off_the_arm_exists_but_is_inert(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "paper_execution_enabled", False)

    with TestClient(main.app) as client:
        arm = main.app.state.paper_execution
        health = client.get("/health").json()

        assert arm.active is False
        assert health["paper_execution"] == {
            "enabled": False, "active": False, "armed_since": None, "opened_today": 0,
            "skipped_today": 0,
        }
        assert health["llm_labeling"]["enabled"] is True  # el etiquetado no depende del brazo

    assert "real_arm_started_at" not in _system_state(bot)


def test_auto_open_without_llm_keeps_the_arm_off_without_stopping_the_bot(
    bot, monkeypatch, caplog,
):
    monkeypatch.setattr(main.settings, "paper_execution_enabled", True)
    monkeypatch.setattr(main.settings, "auto_open_without_llm", True)

    with caplog.at_level(logging.CRITICAL, logger="app.trading.paper_execution"):
        with TestClient(main.app) as client:
            assert main.app.state.paper_execution.active is False
            assert client.get("/health").status_code == 200  # el bot arranco igual

    assert any("AUTO_OPEN_WITHOUT_LLM" in r.getMessage() for r in caplog.records)


def test_a_failure_activating_the_arm_never_prevents_the_bot_from_starting(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "paper_execution_enabled", True)

    async def broken_activate(self):
        raise RuntimeError("la activacion fallo")

    monkeypatch.setattr(main.PaperExecutionArm, "activate", broken_activate)

    with TestClient(main.app) as client:
        assert main.app.state.paper_execution.active is False
        assert client.get("/health").status_code == 200
