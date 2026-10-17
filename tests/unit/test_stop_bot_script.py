"""Contrato de `scripts/stop_bot.ps1` a nivel de texto (el comportamiento real contra
un proceso vivo lo cubre `tests/integration/test_start_stop_bot_scripts.py`, manual,
puerto 8001). Aqui se fijan las verificaciones de seguridad que no deben perderse en
un refactor."""

from __future__ import annotations

from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "scripts" / "stop_bot.ps1").read_text(
    encoding="utf-8"
)


def test_the_launcher_is_recognized_only_by_the_exact_venv_pythonw_of_this_repo():
    assert 'Join-Path $RepoRoot ".venv\\Scripts\\pythonw.exe"' in SCRIPT
    assert "Test-SamePath $Info.ExecutablePath $VenvPythonw" in SCRIPT
    assert "[System.IO.Path]::GetFullPath" in SCRIPT
    assert "OrdinalIgnoreCase" in SCRIPT


def test_a_parent_is_only_trusted_if_it_started_before_its_child():
    assert "$parent.CreationDate -ge $Child.CreationDate" in SCRIPT  # PID reciclado


def test_the_start_bot_cmd_is_verified_by_command_line_and_port():
    assert '$Info.Name -ine "cmd.exe"' in SCRIPT
    assert 'IndexOf($VenvPythonw' in SCRIPT
    assert '"--port $Port(\\s|$)"' in SCRIPT


def test_it_recognizes_the_full_and_the_short_chain_and_leaves_unknown_parents_alone():
    assert "launcher del venv (pythonw.exe)" in SCRIPT
    assert "cmd.exe de start_bot.ps1" in SCRIPT
    assert "-- no se toca." in SCRIPT


def test_it_confirms_that_no_process_of_the_chain_survives_and_fails_otherwise():
    assert "Test-StillAlive" in SCRIPT
    assert "$now.CreationDate -eq $Info.CreationDate" in SCRIPT  # mismo PID Y misma creacion
    assert "Confirmado: no queda vivo ningun proceso de la cadena" in SCRIPT
    assert "sigue vivo $($entry.Role)" in SCRIPT
    assert SCRIPT.count("exit 1") >= 2


def test_it_never_stops_processes_by_name():
    # Nada de `Get-Process -Name python*` ni `taskkill /IM`: solo PIDs verificados.
    lowered = SCRIPT.lower()
    assert "taskkill" not in lowered
    assert "get-process -name" not in lowered
    assert "stop-process -name" not in lowered
