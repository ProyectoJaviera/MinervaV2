from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from app.config import settings
from app.persistence.database import Database
from tests.support import scheduler_tripwire


@pytest.fixture(scope="session", autouse=True)
def _the_real_task_scheduler_is_never_reachable(tmp_path_factory):
    """Ninguna prueba toca el Programador de tareas REAL de Windows: durante toda la sesion,
    `MINERVA_SCHTASKS_EXE` (la unica puerta de los scripts al Programador) apunta a un `schtasks`
    TRAMPA (ver tests/support/scheduler_tripwire.py). Las pruebas de la mudanza la sobrescriben con
    su falso. Si la trampa llegara a usarse en la sesion, esta falla al terminar con la llamada."""
    trap, log = scheduler_tripwire.install(tmp_path_factory.mktemp("tripwire"))
    previous = {k: os.environ.get(k) for k in (scheduler_tripwire.EXE_VARIABLE,
                                               scheduler_tripwire.LOG_VARIABLE)}
    os.environ[scheduler_tripwire.EXE_VARIABLE] = str(trap)
    os.environ[scheduler_tripwire.LOG_VARIABLE] = str(log)
    try:
        yield trap
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    calls = log.read_text(encoding="utf-8", errors="replace").strip() if log.exists() else ""
    assert not calls, (
        "Una prueba intento usar el Programador de tareas REAL (lo freno la trampa): " + calls
    )


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def reveal_open(monkeypatch):
    """Simula "gate abierto + registro oficial del veredicto" en los comandos de Telegram: sin
    esto (el estado real hoy) balance, equity, PnL y PF de la cuenta paper estan ocultos."""
    monkeypatch.setattr("app.telegram.commands.is_outcome_reveal_allowed", lambda *a, **k: True)


@pytest_asyncio.fixture
async def real_db_copy(tmp_path) -> Database:
    """Copia de la base de datos REAL del usuario (`settings.database_path`,
    si existe) en una ruta temporal de pytest (`tmp_path`, se borra sola al
    terminar) -- NUNCA la ruta real. Cualquier test que necesite datos
    reales ya descargados (p.ej. para probar una migracion de esquema o el
    chequeo de disponibilidad de series contra volumenes reales) debe usar
    esta fixture en vez de apuntar un `Database` directamente a
    `settings.database_path`.

    Correccion post-Fase-2 (tarea 1c): varias verificaciones manuales
    anteriores ya usaban copias, pero ad hoc (dentro de `data/`, el mismo
    directorio que el archivo real) -- un `cp`/`rm` mal escrito ahi podria
    haber sobreescrito el archivo real. Esta fixture fuerza la ruta
    temporal por construccion y lo comprueba con un `assert` explicito."""
    real_path = Path(settings.database_path).resolve()
    copy_path = tmp_path / "minerva_real_copy.db"
    assert copy_path.resolve() != real_path, (
        "real_db_copy debe apuntar a una ruta distinta de settings.database_path"
    )
    if real_path.exists():
        shutil.copy2(real_path, copy_path)

    database = Database(str(copy_path))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()
