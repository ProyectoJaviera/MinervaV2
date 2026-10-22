"""Ninguna prueba ni script abre una ventana de consola en Windows.

El sintoma, medido en este PC con un monitor de ventanas: 10 consolas visibles en solo 12
pruebas, todas de `fake_health_server.py`. Causa: se lanzaba con `DETACHED_PROCESS` (sin consola)
a traves del `python.exe` del venv, que es un trampolin de CONSOLA; el interprete real que ese
trampolin lanza como hijo abria su propia consola visible. Con `CREATE_NO_WINDOW` el trampolin
recibe una consola oculta y su hijo la hereda.

Estas pruebas lo vigilan en tres capas:

1. Estatica (AST): toda llamada a `subprocess.run/Popen/call/check_call/check_output` y a
   `asyncio.create_subprocess_*` de `app/`, `scripts/` y `tests/` pasa
   `creationflags=NO_WINDOW`; nada usa `DETACHED_PROCESS` ni `CREATE_NEW_CONSOLE`, `os.system`,
   `os.popen` ni `os.startfile`.
2. PowerShell: todo `Start-Process` de `scripts/*.ps1` y de los .ps1 que generan las pruebas lleva
   `-WindowStyle Hidden` (y nunca `-NoNewWindow`).
3. Empirica: los lanzadores falsos reales (el bot nativo falso y el contenedor falso) no dejan
   ninguna consola visible.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.support.fakes import FakeDocker, FakeNativeBot, free_port, port_open, wait_until
from tests.support.sourcecheck import embedded_strings, powershell_code
from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

PYTHON_ROOTS = ("app", "scripts", "tests")
PROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
ASYNC_CALLS = frozenset({"create_subprocess_exec", "create_subprocess_shell"})
OS_CALLS = frozenset({"system", "popen", "startfile"})
FORBIDDEN_FLAGS = frozenset({"DETACHED_PROCESS", "CREATE_NEW_CONSOLE"})


# --- capa 1: analisis estatico de Python --------------------------------------------------------


def launcher_name(func: ast.expr) -> str | None:
    """`subprocess.run`, `asyncio.create_subprocess_exec`, `os.system`... o None."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module, attr = func.value.id, func.attr
        if (module, attr) in {("subprocess", a) for a in PROCESS_CALLS}:
            return f"subprocess.{attr}"
        if (module, attr) in {("asyncio", a) for a in ASYNC_CALLS}:
            return f"asyncio.{attr}"
        if (module, attr) in {("os", a) for a in OS_CALLS}:
            return f"os.{attr}"
    return None


def process_launch_problems(source: str, filename: str, *, check_flags: bool = True) -> list[str]:
    tree = ast.parse(source)
    hidden_names = {  # `flags = NO_WINDOW | ...` y despues `creationflags=flags`
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and "NO_WINDOW" in ast.unparse(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            hidden_imports = sorted({alias.name for alias in node.names} & PROCESS_CALLS)
            if hidden_imports:
                problems.append(
                    f"{filename}:{node.lineno}: `from subprocess import {hidden_imports}` esconde "
                    "los lanzamientos de este guardian: usa `subprocess.<nombre>`"
                )
        if not isinstance(node, ast.Call):
            continue
        name = launcher_name(node.func)
        if name is None:
            continue
        if name.startswith("os."):
            problems.append(
                f"{filename}:{node.lineno}: {name}() puede abrir una ventana: usa subprocess con "
                "creationflags=NO_WINDOW"
            )
            continue
        keyword = next((kw for kw in node.keywords if kw.arg == "creationflags"), None)
        if keyword is None:
            problems.append(f"{filename}:{node.lineno}: {name}() sin creationflags=NO_WINDOW")
            continue
        names_used = {n.id for n in ast.walk(keyword.value) if isinstance(n, ast.Name)}
        if "NO_WINDOW" not in ast.unparse(keyword.value) and not names_used & hidden_names:
            problems.append(
                f"{filename}:{node.lineno}: {name}(creationflags={ast.unparse(keyword.value)}) "
                "no incluye NO_WINDOW"
            )
    if check_flags:
        for node in ast.walk(tree):
            label = (
                node.attr if isinstance(node, ast.Attribute)
                else node.id if isinstance(node, ast.Name)
                else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else None
            )
            if label in FORBIDDEN_FLAGS:
                problems.append(
                    f"{filename}:{getattr(node, 'lineno', '?')}: usa {label}: el trampolin de "
                    "consola del venv abriria una ventana visible; usa CREATE_NO_WINDOW"
                )
    return problems


def python_files() -> list[Path]:
    return sorted(
        path for root in PYTHON_ROOTS for path in (REPO_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_every_process_launch_in_the_repository_hides_its_console_window():
    problems: list[str] = []
    for path in python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        problems += process_launch_problems(
            path.read_text(encoding="utf-8"), relative, check_flags=path.resolve() != SELF,
        )

    assert not problems, "lanzamientos que pueden abrir una consola:\n" + "\n".join(problems)


BAD_LAUNCHES = [
    "import subprocess\nsubprocess.run(['x'])\n",
    "import subprocess\nsubprocess.Popen(['x'], creationflags=0)\n",
    "import subprocess\n"
    "subprocess.check_output(['x'], creationflags=subprocess.DETACHED_PROCESS)\n",
    "import subprocess\n"
    "subprocess.run(['x'], creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))\n",
    "import subprocess\nsubprocess.run(['x'], creationflags=subprocess.CREATE_NEW_CONSOLE)\n",
    "import os\nos.system('dir')\n",
    "import os\nos.startfile('x')\n",
    "from subprocess import run\nrun(['x'])\n",
    "import asyncio\nasyncio.create_subprocess_exec('x')\n",
]
GOOD_LAUNCHES = [
    "import subprocess\nsubprocess.run(['x'], creationflags=NO_WINDOW)\n",
    "import subprocess\nsubprocess.run(['x'], creationflags=subprocess.CREATE_NO_WINDOW)\n",
    "import subprocess\nsubprocess.run(['x'], creationflags=docker_support.NO_WINDOW)\n",
    "import subprocess\n"
    "subprocess.Popen(['x'], creationflags=NO_WINDOW | CREATE_NEW_PROCESS_GROUP)\n",
    "import subprocess\nflags = NO_WINDOW | 0x200\nsubprocess.Popen(['x'], creationflags=flags)\n",
    "import asyncio\nasyncio.create_subprocess_exec('x', creationflags=NO_WINDOW)\n",
]


@pytest.mark.parametrize("source", BAD_LAUNCHES)
def test_the_guard_catches_every_way_of_opening_a_console(source):
    assert process_launch_problems(source, "muestra.py")


@pytest.mark.parametrize("source", GOOD_LAUNCHES)
def test_the_guard_accepts_launches_that_hide_the_window(source):
    assert process_launch_problems(source, "muestra.py") == []


# --- capa 2: PowerShell -------------------------------------------------------------------------


def start_process_problems(code: str, label: str) -> list[str]:
    problems = []
    if re.search(r"NoNewWindow", code):
        problems.append(f"{label}: usa NoNewWindow; la regla es `-WindowStyle Hidden`")
    for match in re.finditer(r"Start-Process", code):
        window = code[match.start(): match.start() + 400]
        following = window.find("Start-Process", 1)
        if following != -1:
            window = window[:following]
        hidden = re.search(r"-WindowStyle\s+Hidden", window)
        splat = re.search(r"@\w+", window) and re.search(r'WindowStyle\s*=\s*"Hidden"', code)
        if not (hidden or splat):
            line = code.count("\n", 0, match.start()) + 1
            problems.append(f"{label}:{line}: Start-Process sin -WindowStyle Hidden")
    return problems


def test_every_start_process_in_the_powershell_scripts_is_hidden():
    problems: list[str] = []
    for path in sorted((REPO_ROOT / "scripts").glob("*.ps1")):
        code = powershell_code(path.read_text(encoding="utf-8"))
        problems += start_process_problems(code, path.relative_to(REPO_ROOT).as_posix())

    assert not problems, "\n".join(problems)


def test_every_start_process_in_the_powershell_the_tests_generate_is_hidden():
    problems: list[str] = []
    for path in sorted((REPO_ROOT / "tests" / "support").glob("*.py")):
        for text in embedded_strings(path.read_text(encoding="utf-8")):
            if "Start-Process" in text:
                problems += start_process_problems(text, path.relative_to(REPO_ROOT).as_posix())

    assert not problems, "\n".join(problems)


def test_the_powershell_guard_catches_a_visible_start_process():
    assert start_process_problems("Start-Process -FilePath x.exe -Wait", "m.ps1")
    assert start_process_problems("Start-Process -FilePath x.exe -NoNewWindow", "m.ps1")
    assert start_process_problems(
        "Start-Process -FilePath x.exe `\n    -PassThru -WindowStyle Hidden", "m.ps1",
    ) == []
    assert start_process_problems(
        '$a = @{ FilePath = "x"; WindowStyle = "Hidden" }\nStart-Process @a', "m.ps1",
    ) == []
    assert start_process_problems("Start-Process @a", "m.ps1")  # el splat no oculta nada


# --- capa 3: comprobacion empirica ----------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="ventanas de consola de Windows")
def test_the_fake_servers_the_tests_start_open_no_visible_console_window(tmp_path):
    """Arranca de verdad los dos lanzadores falsos que abrian una consola por prueba (el bot
    nativo falso y el `docker compose up -d` falso) y mira, con la API de ventanas de Windows, que
    no quede ninguna consola visible de `fake_health_server.py`."""
    from tests.support import window_probe

    bot = FakeNativeBot(tmp_path, free_port())
    docker = FakeDocker(tmp_path)
    docker_port = free_port()
    before = {w.hwnd for w in window_probe.console_windows()}
    try:
        bot.start()
        done = subprocess.run(
            [str(docker.cmd), "compose", "-p", "sondeo", "up", "-d"],
            env=docker.env(MINERVA_PORT=str(docker_port), COMPOSE_PROJECT_NAME="sondeo"),
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )
        assert done.returncode == 0, done.stderr
        assert wait_until(lambda: port_open(docker_port)), "el contenedor falso no abrio su puerto"
        time.sleep(1.5)  # una consola visible aparece en milisegundos; se deja margen de sobra

        offenders = [
            w for w in window_probe.console_windows()
            if w.hwnd not in before and "fake_health_server" in (w.cmd + w.title)
        ]

        assert not offenders, f"consolas visibles de los servidores falsos: {offenders}"
    finally:
        bot.shutdown()
        docker.shutdown()
