"""Alerta externa de caida por Telegram (pre-Fase 5, item 1 del plan aprobado
2026-10-14): `scripts/telegram_alert.py`. Nunca una llamada real a Telegram ni
a `/health` -- `check_health_fn`/`send_fn` se inyectan (mismo estilo que
`FakeLlmClient`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.check_health import OK, SIN_RESPUESTA, STALE
from scripts.telegram_alert import run

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeSender:
    def __init__(self, succeeds: bool = True) -> None:
        self.calls: list[dict] = []
        self._succeeds = succeeds

    def __call__(self, token, chat_id, text, *, timeout=10.0) -> bool:
        self.calls.append({"token": token, "chat_id": chat_id, "text": text})
        return self._succeeds


def _check_health_returning(status_code: int):
    def _fn(host, port, timeout):
        print(f"estado simulado {status_code}")
        return status_code

    return _fn


def _run(tmp_path, status_code, sender, *, now=NOW, reminder_minutes=60.0):
    return run(
        host="127.0.0.1", port=8000, timeout=5.0, state_path=tmp_path / "state.json",
        reminder_minutes=reminder_minutes, token="t", chat_id="c", now=now,
        check_health_fn=_check_health_returning(status_code), send_fn=sender,
    )


def _write_state(tmp_path, **fields) -> None:
    defaults = {"last_status": None, "down_since": None, "last_notified_at": None}
    defaults.update(fields)
    (tmp_path / "state.json").write_text(json.dumps(defaults), encoding="utf-8")


# --- transiciones de estado -----------------------------------------------------


def test_ok_to_ok_never_notifies(tmp_path):
    _write_state(tmp_path, last_status="OK")
    sender = FakeSender()

    ok = _run(tmp_path, OK, sender)

    assert ok is True
    assert sender.calls == []


def test_ok_to_stale_notifies_and_updates_state(tmp_path):
    _write_state(tmp_path, last_status="OK")
    sender = FakeSender()

    ok = _run(tmp_path, STALE, sender)

    assert ok is True
    assert len(sender.calls) == 1
    assert "STALE" in sender.calls[0]["text"]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state == {"last_status": "STALE", "down_since": NOW.isoformat(),
                      "last_notified_at": NOW.isoformat()}


def test_stale_to_stale_under_the_reminder_window_does_not_notify(tmp_path):
    ten_min_ago = (NOW - timedelta(minutes=10)).isoformat()
    _write_state(tmp_path, last_status="STALE", down_since=ten_min_ago,
                 last_notified_at=ten_min_ago)
    sender = FakeSender()

    ok = _run(tmp_path, STALE, sender, reminder_minutes=60.0)

    assert ok is True
    assert sender.calls == []


def test_stale_to_stale_over_the_reminder_window_notifies_again(tmp_path):
    two_hours_ago = (NOW - timedelta(hours=2)).isoformat()
    _write_state(tmp_path, last_status="STALE", down_since=two_hours_ago,
                 last_notified_at=two_hours_ago)
    sender = FakeSender()

    ok = _run(tmp_path, STALE, sender, reminder_minutes=60.0)

    assert ok is True
    assert len(sender.calls) == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    # down_since se preserva: la racha sigue siendo la misma de hace 2 horas.
    assert state["down_since"] == two_hours_ago
    assert state["last_notified_at"] == NOW.isoformat()


def test_stale_to_sin_respuesta_notifies_immediately_and_keeps_down_since(tmp_path):
    """Cambio de CAUSA dentro de la misma racha de caida -- avisa ya, no espera
    el recordatorio, pero `down_since` sigue siendo el de la racha original."""
    thirty_min_ago = (NOW - timedelta(minutes=30)).isoformat()
    _write_state(tmp_path, last_status="STALE", down_since=thirty_min_ago,
                 last_notified_at=thirty_min_ago)
    sender = FakeSender()

    ok = _run(tmp_path, SIN_RESPUESTA, sender, reminder_minutes=60.0)

    assert ok is True
    assert len(sender.calls) == 1
    assert "SIN_RESPUESTA" in sender.calls[0]["text"]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["down_since"] == thirty_min_ago
    assert state["last_status"] == "SIN_RESPUESTA"


def test_recovery_to_ok_notifies_and_clears_down_since(tmp_path):
    down_since = (NOW - timedelta(hours=3)).isoformat()
    _write_state(tmp_path, last_status="SIN_RESPUESTA", down_since=down_since,
                 last_notified_at=down_since)
    sender = FakeSender()

    ok = _run(tmp_path, OK, sender)

    assert ok is True
    assert len(sender.calls) == 1
    assert "volvio a OK" in sender.calls[0]["text"]
    assert down_since in sender.calls[0]["text"]  # menciona desde cuando estuvo caido
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state == {"last_status": "OK", "down_since": None, "last_notified_at": NOW.isoformat()}


def test_initial_state_with_the_bot_already_down_notifies_immediately(tmp_path):
    """Sin archivo de estado (primera corrida o se borro) y el bot YA caido:
    ante la duda, el sesgo del monitor debe ser avisar, no quedarse callado."""
    assert not (tmp_path / "state.json").exists()
    sender = FakeSender()

    ok = _run(tmp_path, STALE, sender)

    assert ok is True
    assert len(sender.calls) == 1


def test_initial_state_with_the_bot_ok_never_notifies(tmp_path):
    assert not (tmp_path / "state.json").exists()
    sender = FakeSender()

    ok = _run(tmp_path, OK, sender)

    assert ok is True
    assert sender.calls == []


# --- fallo de envio: el estado no se actualiza, se reintenta despues -----------


def test_a_failed_send_does_not_update_the_state_file(tmp_path):
    _write_state(tmp_path, last_status="OK")
    sender = FakeSender(succeeds=False)

    ok = _run(tmp_path, STALE, sender)

    assert ok is False  # el pipeline de alertado fallo
    assert len(sender.calls) == 1  # si lo intento
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state == {"last_status": "OK", "down_since": None, "last_notified_at": None}


def test_the_next_poll_retries_a_pending_alert_after_a_failed_send(tmp_path):
    _write_state(tmp_path, last_status="OK")
    failing_sender = FakeSender(succeeds=False)
    _run(tmp_path, STALE, failing_sender, now=NOW)

    working_sender = FakeSender(succeeds=True)
    ok = _run(tmp_path, STALE, working_sender, now=NOW + timedelta(minutes=10))

    assert ok is True
    assert len(working_sender.calls) == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["last_status"] == "STALE"


# --- nunca se loguea el token/chat_id -------------------------------------------


def test_send_telegram_message_never_logs_the_token_or_chat_id_on_failure(capsys, monkeypatch):
    import httpx

    from scripts.telegram_alert import send_telegram_message

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(httpx, "post", _boom)

    sent = send_telegram_message("super-secreto-token", "chat-id-123", "hola")

    assert sent is False
    captured = capsys.readouterr()
    assert "super-secreto-token" not in captured.out
    assert "chat-id-123" not in captured.out


@pytest.mark.parametrize("path_str", ["data/telegram_alert_state.json"])
def test_default_state_file_lives_under_the_gitignored_data_directory(path_str):
    from scripts.telegram_alert import DEFAULT_STATE_FILE

    assert DEFAULT_STATE_FILE == path_str
    assert Path(path_str).parts[0] == "data"
