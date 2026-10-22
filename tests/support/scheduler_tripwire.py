"""Red de seguridad: ninguna prueba debe llegar al Programador de tareas REAL de Windows
(`MinervaStartBot`, `MinervaHealthAlert`, `MinervaDailySummary`, ...).

Los scripts de la mudanza (`scripts/docker_common.ps1`) hablan con el Programador SOLO a traves
de `$env:MINERVA_SCHTASKS_EXE` (por defecto, el `schtasks` real). `tests/conftest.py` apunta esa
variable, durante TODA la sesion de pruebas, a un `schtasks` TRAMPA que no toca nada: registra la
llamada en `MINERVA_TRIPWIRE_LOG` y sale con codigo 99. Las pruebas de la mudanza la sobrescriben
con su `schtasks` falso (`tests/support/fake_schtasks.py`). Si otra prueba (o una futura) lanza
esos scripts sin su propio falso, el peor efecto es "la tarea no existe": jamas se deshabilita
una tarea real, y al terminar la sesion la llamada queda a la vista y la sesion falla.
"""

from __future__ import annotations

from pathlib import Path

LOG_VARIABLE = "MINERVA_TRIPWIRE_LOG"
EXE_VARIABLE = "MINERVA_SCHTASKS_EXE"

SCRIPT = (
    "@echo off\r\n"
    f'if defined {LOG_VARIABLE} echo %*>>"%{LOG_VARIABLE}%"\r\n'
    "echo TRIPWIRE: una prueba intento usar el Programador de tareas REAL (schtasks %*) 1>&2\r\n"
    "exit /b 99\r\n"
)


def install(directory: Path) -> tuple[Path, Path]:
    """Crea el `schtasks` trampa y devuelve (ejecutable, archivo de registro)."""
    directory.mkdir(parents=True, exist_ok=True)
    trap = directory / "schtasks_trampa.cmd"
    trap.write_bytes(SCRIPT.encode("ascii"))
    return trap, directory / "schtasks_trampa.log"
