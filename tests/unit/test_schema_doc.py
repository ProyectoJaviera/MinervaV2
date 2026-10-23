"""Fase 6: `docs/ESQUEMA_BD.md` se GENERA desde el DDL real (`scripts/export_schema_doc.py`); esta
prueba falla si el documento versionado se desincroniza del codigo, y cubre el analisis del DDL."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app.persistence import database as database_module
from app.persistence.database import Database
from scripts import export_schema_doc as schema_doc

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "ESQUEMA_BD.md"


def test_the_versioned_document_matches_the_ddl_in_the_code():
    expected = schema_doc.build_document()

    current = DOC.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert current == expected, (
        "docs/ESQUEMA_BD.md esta desactualizado: corre `python scripts/export_schema_doc.py`"
    )


def test_every_table_of_a_fresh_database_is_documented_exactly_once():
    async def tables() -> list[str]:
        db = Database(":memory:")
        await db.connect()
        try:
            rows = await db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
            return sorted(r["name"] for r in rows)
        finally:
            await db.close()

    text = DOC.read_text(encoding="utf-8")
    names = asyncio.run(tables())

    assert len(names) == 28
    for name in names:
        assert len(re.findall(rf"^### {name}$", text, flags=re.MULTILINE)) == 1, name
    assert f"**{len(names)} tablas**" in text


def test_a_table_without_a_group_stops_the_generation(monkeypatch):
    monkeypatch.setattr(
        schema_doc, "GROUPS", tuple(
            (title, description, tuple(n for n in names if n != "control_audit"))
            for title, description, names in schema_doc.GROUPS
        ),
    )

    with pytest.raises(SystemExit, match="control_audit"):
        schema_doc.build_document()


def test_a_group_naming_a_table_that_no_longer_exists_stops_the_generation(monkeypatch):
    title, description, names = schema_doc.GROUPS[0]
    monkeypatch.setattr(
        schema_doc, "GROUPS", ((title, description, (*names, "tabla_fantasma")),
                               *schema_doc.GROUPS[1:]),
    )

    with pytest.raises(SystemExit, match="tabla_fantasma"):
        schema_doc.build_document()


def test_the_comment_before_each_create_table_becomes_its_description():
    sql = """
CREATE TABLE IF NOT EXISTS sin_comentario (id INTEGER);

-- Primera linea
-- segunda linea.
CREATE TABLE IF NOT EXISTS con_comentario (id INTEGER);

-- Comentario que NO es de nadie (hay una linea en blanco)

CREATE TABLE IF NOT EXISTS otra (id INTEGER);

-- Un indice no consume el comentario de la tabla siguiente
CREATE INDEX IF NOT EXISTS idx ON otra (id);
CREATE TABLE IF NOT EXISTS ultima (id INTEGER);
"""

    comments = schema_doc.table_comments(sql)

    assert comments == {
        "sin_comentario": "", "con_comentario": "Primera linea segunda linea.", "otra": "",
        "ultima": "",
    }


def test_the_real_schema_comments_are_picked_up():
    comments = schema_doc.table_comments(database_module.SCHEMA)

    assert "Cada rechazo del motor de riesgo" in comments["risk_rejections"]
    assert "reclamo idempotente" in comments["paper_execution_log"]


@pytest.mark.parametrize(("sql", "expected"), [
    ("CREATE TABLE t (a TEXT CHECK (a IN ('x', 'y')), b INT)", ["a IN ('x', 'y')"]),
    ("CREATE TABLE t (a INT CHECK (a > 0 AND (a < 10 OR a = 20)), b INT CHECK (b >= 0))",
     ["a > 0 AND (a < 10 OR a = 20)", "b >= 0"]),
    ("CREATE TABLE t (a INT)", []),
    ("CREATE TABLE t (a TEXT CHECK (a IN (\n  'x',\n  'y'\n)))", ["a IN ( 'x', 'y' )"]),
])
def test_check_constraints_are_extracted_with_balanced_parentheses(sql, expected):
    assert schema_doc.extract_checks(sql) == expected


def test_the_document_cites_the_moved_phase_documents_at_their_current_path():
    text = DOC.read_text(encoding="utf-8")

    assert "docs/historial/FASE3_PLAN.md" in text  # el comentario del DDL dice docs/FASE3_PLAN.md
    for name in schema_doc.MOVED_TO_HISTORY:
        assert f"docs/{name}.md" not in text.replace(f"docs/historial/{name}.md", ""), name


def test_legacy_paths_are_rewritten_only_for_moved_documents():
    assert schema_doc.modernize_doc_paths("ver docs/FASE2_PLAN.md y docs/FASE3_6_LLM.md") == (
        "ver docs/historial/FASE2_PLAN.md y docs/FASE3_6_LLM.md"
    )
    assert schema_doc.modernize_doc_paths("docs/FASE2_REEJECUCION_TABLAS.md") == (
        "docs/historial/FASE2_REEJECUCION_TABLAS.md"
    )


def test_the_document_hides_nothing_about_result_columns_but_holds_no_data():
    text = DOC.read_text(encoding="utf-8")

    # El esquema SI describe las columnas de resultados (es el modelo de datos)...
    assert "`pnl_net_usdt`" in text
    # ... pero es solo estructura: ningun valor de ninguna fila.
    assert "No guarda datos del chat" in text  # el comentario del DDL de control_audit
    assert "INSERT INTO" not in text
