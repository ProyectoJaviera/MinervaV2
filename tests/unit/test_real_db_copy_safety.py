"""Tests de la fixture `real_db_copy` (tarea 1c) y de que la base de datos
real del usuario tiene el esquema que el codigo espera.

Estos tests son la version automatizada/permanente de varias
verificaciones manuales hechas durante esta sesion (con copias ad hoc en
`data/`, nunca sobre el archivo real, pero sin un `assert` que lo
garantizara por construccion) -- de ahora en adelante, cualquier test que
necesite datos reales usa `real_db_copy` en vez de apuntar directamente a
`settings.database_path`."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.persistence.repositories.backtest_repo import _VERDICT_COLUMNS


def test_real_db_copy_path_is_never_the_real_path(real_db_copy, tmp_path):
    real_path = Path(settings.database_path).resolve()
    assert Path(real_db_copy.path).resolve() != real_path
    assert Path(real_db_copy.path).resolve().parent == tmp_path.resolve()


@pytest.mark.asyncio
async def test_writing_to_the_copy_never_touches_the_real_file(real_db_copy):
    real_path = Path(settings.database_path)
    if not real_path.exists():
        pytest.skip("no hay data/minerva.db real en este entorno")

    before = real_path.stat().st_mtime_ns
    before_size = real_path.stat().st_size

    await real_db_copy.execute(
        "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("test_real_db_copy_safety", "deliberately written to the COPY only", "2026-01-01"),
    )

    assert real_path.stat().st_mtime_ns == before
    assert real_path.stat().st_size == before_size
    row = await real_db_copy.fetch_one(
        "SELECT value FROM system_state WHERE key = ?", ("test_real_db_copy_safety",)
    )
    assert row is not None  # si se escribio, fue en la copia


@pytest.mark.asyncio
async def test_real_database_backtest_verdicts_has_all_expected_columns(real_db_copy):
    """Version automatizada de la verificacion manual hecha en esta
    sesion: la tabla real `backtest_verdicts` (tras la migracion
    automatica en `Database.connect()`) debe tener TODAS las columnas que
    `backtest_repo._VERDICT_COLUMNS` usa en el INSERT -- el orden fisico
    de la tabla no importa (el INSERT usa columnas con nombre, no
    posicion), solo que existan."""
    real_path = Path(settings.database_path)
    if not real_path.exists():
        pytest.skip("no hay data/minerva.db real en este entorno")

    cursor = await real_db_copy.conn.execute("PRAGMA table_info(backtest_verdicts)")
    rows = await cursor.fetchall()
    actual_columns = {row[1] for row in rows}

    missing = [c for c in _VERDICT_COLUMNS if c not in actual_columns]
    assert missing == [], f"columnas esperadas por el INSERT pero ausentes en la tabla: {missing}"
