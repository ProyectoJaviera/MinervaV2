"""Alerta externa de caida por Telegram (pre-Fase 5, item 1 del plan aprobado
2026-10-14): `scripts/telegram_alert.py`. Nunca una llamada real a Telegram ni
a `/health` -- `check_health_fn`/`send_fn` se inyectan (mismo estilo que
`FakeLlmClient`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


# --- robustez del archivo de estado: JSON corrupto y escritura atomica ---------


def test_load_state_treats_corrupt_json_as_no_state(tmp_path):
    from scripts.telegram_alert import _EMPTY_STATE, _load_state

    state_path = tmp_path / "state.json"
    state_path.write_text("{esto no es json valido", encoding="utf-8")

    assert _load_state(state_path) == _EMPTY_STATE


def test_load_state_treats_unreadable_bytes_as_no_state(tmp_path):
    """Bytes que ni siquiera decodifican como UTF-8 (no solo JSON invalido)
    tambien deben tratarse como "sin estado", no tumbar el script."""
    from scripts.telegram_alert import _EMPTY_STATE, _load_state

    state_path = tmp_path / "state.json"
    state_path.write_bytes(b"\xff\xfe\x00\x01")

    assert _load_state(state_path) == _EMPTY_STATE


def test_a_corrupt_state_file_still_lets_run_notify_as_if_it_were_a_cold_start(tmp_path):
    (tmp_path / "state.json").write_text("{corrupto", encoding="utf-8")
    sender = FakeSender()

    ok = _run(tmp_path, STALE, sender)

    assert ok is True
    assert len(sender.calls) == 1  # se trato como arranque en frio con el bot caido -> avisa


def test_save_state_writes_atomically_via_a_tmp_file_and_os_replace(tmp_path, monkeypatch):
    from scripts import telegram_alert
    from scripts.telegram_alert import _save_state

    state_path = tmp_path / "state.json"
    tmp_file_path = tmp_path / "state.json.tmp"
    calls: list[tuple[str, str]] = []
    original_replace = os.replace

    def _tracking_replace(src, dst):
        # En el momento del replace, el .tmp ya debe existir con el
        # contenido final -- confirma que se escribe ANTES de reemplazar,
        # no que `_save_state` solo simula la secuencia.
        assert Path(src).exists()
        calls.append((Path(src).name, Path(dst).name))
        return original_replace(src, dst)

    monkeypatch.setattr(telegram_alert.os, "replace", _tracking_replace)

    _save_state(state_path, {"last_status": "OK", "down_since": None, "last_notified_at": None})

    assert calls == [("state.json.tmp", "state.json")]
    assert state_path.exists()
    assert not tmp_file_path.exists()  # consumido por os.replace, no queda huerfano
    assert json.loads(state_path.read_text(encoding="utf-8"))["last_status"] == "OK"


# --- funciona sin importar el directorio de trabajo desde el que arranca -------


def test_running_the_script_from_an_unrelated_cwd_does_not_fail_to_import(tmp_path):
    """Reproduce el bug real (2026-10-14): el Programador de tareas de
    Windows arranca en un directorio ajeno (tipicamente System32, no la raiz
    del repo) -- antes de `os.chdir` al inicio del script, `sys.path.insert(
    0, ".")` resolvia a ESE directorio y rompia el import de `app.config`/
    `scripts.check_health` con `ModuleNotFoundError`. Corre el script de
    verdad, por subprocess, con el cwd puesto a un directorio temporal
    ajeno al repo -- `--help` alcanza para probar el import sin necesitar
    TELEGRAM_BOT_TOKEN/CHAT_ID (argparse sale antes de llegar a ese chequeo)."""
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "telegram_alert.py"
    assert script_path.exists()

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "ModuleNotFoundError" not in result.stderr
    assert "--reminder-minutes" in result.stdout
