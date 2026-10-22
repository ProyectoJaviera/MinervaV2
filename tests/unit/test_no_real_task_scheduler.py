"""Ninguna prueba toca el Programador de tareas REAL de Windows (`MinervaStartBot`,
`MinervaHealthAlert`, `MinervaDailySummary`, `MinervaBackup`): todo pasa por una puerta unica y
esa puerta, en las pruebas, es siempre un falso o una trampa.

La unica via del repositorio al Programador es `scripts/docker_common.ps1`, y lo hace SOLO por
`$env:MINERVA_SCHTASKS_EXE` (por defecto, el `schtasks` real). Garantias:

1. Estatica: ningun otro codigo (.ps1, .py de `app/`, `scripts/` y `tests/`) llama a `schtasks` ni a
   los cmdlets `*-ScheduledTask`; la mencion en un docstring o un comentario no cuenta.
2. Durante toda la sesion, `tests/conftest.py` deja `MINERVA_SCHTASKS_EXE` apuntando a una TRAMPA
   (`tests/support/scheduler_tripwire.py`) que no toca nada; las pruebas de la mudanza la
   sobrescriben con su `schtasks` falso, de tarea `MinervaStartBotTest` (nunca un nombre real).
3. Funcional: un script que NO trae su falso llega a la trampa, no al `schtasks` real.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import scheduler_tripwire
from tests.support.fakes import MigrationWorld
from tests.support.sourcecheck import embedded_strings, powershell_code
from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_TASKS = ("MinervaStartBot", "MinervaHealthAlert", "MinervaDailySummary", "MinervaBackup")
# Cualquier forma de hablar con el Programador de tareas.
SCHEDULER = re.compile(r"schtasks|ScheduledTask|Schedule\.Service|ScheduledJob|Taskschd", re.I)
# Los cmdlets y el COM del Programador (el `schtasks` de `docker_common.ps1` se trata aparte).
SCHEDULER_API = re.compile(r"ScheduledTask|Schedule\.Service|ScheduledJob|Taskschd", re.I)
# Archivos que SI nombran al Programador porque son el falso, la trampa o su cableado.
MAY_MENTION_THE_SCHEDULER = frozenset({
    "tests/conftest.py",
    "tests/support/fake_schtasks.py",
    "tests/support/fakes.py",
    "tests/support/scheduler_tripwire.py",
    "tests/unit/test_no_real_task_scheduler.py",
})


def repo_python_files() -> list[Path]:
    return sorted(
        path for root in ("app", "scripts", "tests") for path in (REPO_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_only_the_scheduler_seam_of_docker_common_can_reach_schtasks():
    problems: list[str] = []
    for path in sorted((REPO_ROOT / "scripts").glob("*.ps1")):
        label = path.relative_to(REPO_ROOT).as_posix()
        code = powershell_code(path.read_text(encoding="utf-8"))
        for number, line in enumerate(code.splitlines(), 1):
            if SCHEDULER_API.search(line):
                problems.append(f"{label}:{number}: usa la API del Programador: {line.strip()}")
            elif re.search(r"schtasks", line, re.I) and not re.search(
                r"SchtasksExe|MINERVA_SCHTASKS_EXE", line,
            ):
                problems.append(f"{label}:{number}: llama a schtasks sin la puerta: {line.strip()}")

    assert not problems, "\n".join(problems)


def test_no_python_code_talks_to_the_task_scheduler():
    problems = []
    for path in repo_python_files():
        label = path.relative_to(REPO_ROOT).as_posix()
        if label in MAY_MENTION_THE_SCHEDULER:
            continue
        for text in embedded_strings(path.read_text(encoding="utf-8"), with_docstrings=False):
            if SCHEDULER.search(text):
                problems.append(f"{label}: una cadena menciona al Programador: {text[:80]!r}")

    assert not problems, "\n".join(problems)


def test_the_fakes_never_use_the_name_of_a_real_task():
    assert MigrationWorld.TASK not in REAL_TASKS
    for fragment in ("HealthAlert", "DailySummary", "Backup"):
        assert fragment not in MigrationWorld.TASK


def test_during_the_tests_the_scheduler_gate_points_at_the_tripwire_not_at_schtasks():
    gate = Path(os.environ[scheduler_tripwire.EXE_VARIABLE])

    assert gate.name == "schtasks_trampa.cmd"
    assert gate.is_file()
    assert "schtasks.exe" not in str(gate).lower()


def test_a_migration_world_replaces_the_tripwire_with_its_own_fake(tmp_path):
    world = MigrationWorld(tmp_path, native_running=False)
    try:
        gate = Path(world.env()[scheduler_tripwire.EXE_VARIABLE])

        assert gate == world.tasks.cmd
        assert gate.parent == tmp_path / "fake_bin"
    finally:
        world.shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
def test_a_script_without_its_own_fake_reaches_the_tripwire_and_never_the_real_scheduler(tmp_path):
    # Se carga docker_common.ps1 TAL CUAL lo usa la mudanza, con el entorno heredado de la sesion
    # (la trampa) y los nombres de las tareas REALES: la trampa registra las dos llamadas y las
    # rechaza; si hubiera llegado al `schtasks` real, la tarea se habria deshabilitado de verdad.
    log = tmp_path / "trampa.log"
    script = tmp_path / "probe.ps1"
    script.write_text(
        f'. "{REPO_ROOT / "scripts" / "docker_common.ps1"}"\n'
        'foreach ($name in "MinervaStartBot", "MinervaHealthAlert", "MinervaDailySummary") {\n'
        '    Write-Output ("estado:{0}={1}" -f $name, (Get-TaskState $name))\n'
        '    try { Set-TaskEnabled $name $false; Write-Output "cambio:$name=aceptado" }\n'
        '    catch { Write-Output "cambio:$name=rechazado" }\n'
        "}\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment[scheduler_tripwire.LOG_VARIABLE] = str(log)

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=120, env=environment, creationflags=NO_WINDOW,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("MinervaStartBot", "MinervaHealthAlert", "MinervaDailySummary"):
        assert f"estado:{name}=Missing" in result.stdout  # la trampa responde "no existe"
        assert f"cambio:{name}=rechazado" in result.stdout  # y nunca deja cambiarla
    calls = log.read_text(encoding="utf-8", errors="replace").upper()
    assert calls.count("/QUERY") == 3 and calls.count("/CHANGE") == 3
    for name in ("MinervaStartBot", "MinervaHealthAlert", "MinervaDailySummary"):
        assert name.upper() in calls
