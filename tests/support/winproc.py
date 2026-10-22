"""Procesos hijos de las pruebas SIN ventana de consola (Windows).

Toda llamada a `subprocess.run`/`Popen` de `tests/` pasa `creationflags=NO_WINDOW`
(`tests/unit/test_no_console_windows.py` lo exige). Motivo, medido en este PC: el `python.exe` de
un venv de uv es un trampolin de CONSOLA que lanza el interprete real como hijo. Si el proceso se
crea con `DETACHED_PROCESS` (sin consola) o desde un padre que no tiene consola, ese hijo abre una
consola VISIBLE; con `CREATE_NO_WINDOW` el trampolin recibe una consola oculta y su hijo la hereda.

En el resto de sistemas vale 0 (`creationflags=0` es valido en POSIX).
"""

from __future__ import annotations

import subprocess

NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
