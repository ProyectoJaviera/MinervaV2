from __future__ import annotations

import shutil
from pathlib import Path

import pytest_asyncio

from app.config import settings
from app.persistence.database import Database


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


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
