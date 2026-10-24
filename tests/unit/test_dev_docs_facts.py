"""Pruebas de `scripts/dev_docs_facts.py`: los hechos que se derivan del codigo para `docs/dev/`.

Todo con repositorios sinteticos en `tmp_path`: el modulo solo lee archivos con `ast`, nunca importa
`app` ni lee `.env`. Cada hecho tiene un caso que debe aparecer y otro que NO debe aparecer.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from scripts import dev_docs_facts as facts_module


def write(repo: Path, relative: str, body: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


SCHEMA = '''
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS trades (id INTEGER);
    CREATE TABLE IF NOT EXISTS shadow_trades (id INTEGER);
    CREATE TABLE IF NOT EXISTS system_state (key TEXT, value TEXT);
    CREATE TABLE IF NOT EXISTS signals (id INTEGER);
    CREATE TRIGGER IF NOT EXISTS guard BEFORE UPDATE OF label ON shadow_trades BEGIN SELECT 1; END;
    """
'''


def make_repo(tmp_path: Path) -> Path:
    write(tmp_path, "app/__init__.py", '"""Paquete raiz."""\n')
    write(tmp_path, "app/persistence/__init__.py", "")
    write(tmp_path, "app/persistence/database.py", SCHEMA)
    write(tmp_path, "app/persistence/repositories/__init__.py", "")
    return tmp_path


# --- nombres y paginas -------------------------------------------------------------------------

def test_module_name_and_package_of_follow_the_file_tree():
    assert facts_module.module_name("app/trading/risk_engine.py") == "app.trading.risk_engine"
    assert facts_module.module_name("app/trading/__init__.py") == "app.trading"
    assert facts_module.module_name("scripts/check_health.py") == "scripts.check_health"
    assert facts_module.package_of("app/trading/risk_engine.py") == "trading"
    assert facts_module.package_of("app/config.py") == "config"
    assert facts_module.package_of("app/main.py") == "main"
    assert facts_module.package_of("app/__init__.py") == "main"
    assert facts_module.package_of("scripts/x.py") == "scripts"


def test_private_modules_have_no_reference_page():
    page = facts_module.reference_page("app/trading/risk_engine.py")
    assert page == "app/trading/risk_engine.html"
    assert facts_module.reference_page("app/trading/__init__.py") == "app/trading.html"
    assert facts_module.reference_page("app/llm/_anthropic_client.py") is None
    assert facts_module.reference_page("app/_private/x.py") is None


# --- imports -----------------------------------------------------------------------------------

def test_imports_resolve_absolute_relative_and_submodule_forms(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/persistence/repositories/shadow_repo.py", "X = 1\n")
    write(repo, "app/trading/__init__.py", "")
    write(repo, "app/trading/risk.py", "Y = 2\n")
    write(repo, "app/trading/user.py", """
        from app.persistence.repositories import shadow_repo
        from app.trading import risk as risk_alias
        from app.persistence.database import SCHEMA
        from . import risk
        from .risk import Y
        import app.persistence.database as database
    """)
    sources = facts_module.load_sources(repo)
    known = frozenset(source.module for source in sources.values())

    edges, bindings = facts_module.import_bindings(sources["app/trading/user.py"], known)

    assert edges == {
        "app.persistence.repositories.shadow_repo", "app.trading.risk", "app.persistence.database",
    }
    assert bindings["shadow_repo"] == ("app.persistence.repositories.shadow_repo", None)
    assert bindings["risk_alias"] == ("app.trading.risk", None)
    assert bindings["SCHEMA"] == ("app.persistence.database", "SCHEMA")
    assert bindings["Y"] == ("app.trading.risk", "Y")
    assert bindings["database"] == ("app.persistence.database", None)


def test_imports_of_the_standard_library_and_third_parties_are_ignored(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/x.py", "import os\nfrom pathlib import Path\nimport fastapi\n")
    sources = facts_module.load_sources(repo)
    edges, _ = facts_module.import_bindings(
        sources["app/x.py"], frozenset(s.module for s in sources.values())
    )
    assert edges == set()


def test_lazy_imports_inside_functions_count(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/a.py", "A = 1\n")
    write(repo, "app/b.py", "def f():\n    from app import a\n    return a.A\n")
    facts = facts_module.collect_facts(repo)
    assert facts["app/b.py"].imports == ("app.a",)
    assert facts["app/a.py"].imported_by == ("app.b",)


# --- configuracion -----------------------------------------------------------------------------

CONFIG = '''
    from pydantic import Field
    from pydantic_settings import BaseSettings

    class Settings(BaseSettings):
        env: str = Field(default="development", alias="ENV")
        leverage: int = 10
        max_drawdown_pct: float = 20.0

        @property
        def news_sources_list(self):
            return []

        def _private(self):
            return 1
'''


def test_settings_catalog_uses_the_alias_and_lists_public_methods(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/config.py", CONFIG)
    fields, methods = facts_module.settings_catalog(facts_module.load_sources(repo))
    assert fields == {"env": "ENV", "leverage": "LEVERAGE", "max_drawdown_pct": "MAX_DRAWDOWN_PCT"}
    assert methods == {"news_sources_list"}


def test_settings_used_reads_only_settings_receivers(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/config.py", CONFIG)
    write(repo, "app/user.py", """
        def f(settings, trade, self):
            a = settings.max_drawdown_pct          # campo de Settings
            b = settings.news_sources_list         # propiedad
            c = trade.leverage                     # NO es Settings aunque el nombre coincida
            d = settings.unknown_attribute         # no existe en Settings
            return a, b, c, d

        class K:
            def m(self):
                return self._settings.env
    """)
    sources = facts_module.load_sources(repo)
    fields, methods = facts_module.settings_catalog(sources)

    used = facts_module.settings_used(sources["app/user.py"], fields, methods)

    assert used == {"MAX_DRAWDOWN_PCT", "news_sources_list", "ENV"}
    assert facts_module.settings_used(sources["app/config.py"], fields, methods) == set()


# --- tablas ------------------------------------------------------------------------------------

def access_of(tmp_path: Path, relative: str) -> facts_module.Access:
    sources = facts_module.load_sources(tmp_path)
    known = frozenset(s.module for s in sources.values())
    bindings = {path: facts_module.import_bindings(s, known)[1] for path, s in sources.items()}
    tables = facts_module.table_catalog(sources)
    return facts_module.table_facts(sources, tables, bindings)[relative]


def test_table_catalog_reads_the_schema_without_importing_it(tmp_path):
    repo = make_repo(tmp_path)
    assert facts_module.table_catalog(facts_module.load_sources(repo)) == {
        "trades", "shadow_trades", "system_state", "signals",
    }


def test_table_catalog_ignores_the_ddl_phrase_quoted_in_prose(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/persistence/database.py", SCHEMA + '''
    def migrate():
        """`CREATE TABLE IF NOT EXISTS` no agrega columnas; CREATE TABLE IF NOT EXISTS no aplica."""
    ''')

    tables = facts_module.table_catalog(facts_module.load_sources(repo))

    assert tables == {"trades", "shadow_trades", "system_state", "signals"}


def test_sql_reads_writes_and_ddl_are_told_apart(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/user.py", '''
        async def f(db):
            await db.execute("SELECT * FROM trades t JOIN signals s ON s.id = t.id")
            await db.execute("INSERT OR IGNORE INTO shadow_trades (id) VALUES (1)")
            await db.execute("UPDATE system_state SET value = 1 WHERE key = 'k'")
            await db.execute("DELETE FROM signals WHERE id = 1")
            await db.execute("INSERT INTO trades (id) SELECT id FROM shadow_trades")
            await db.execute("ALTER TABLE trades ADD COLUMN extra TEXT")
    ''')

    access = access_of(repo, "app/user.py")

    assert access.reads == {"trades", "signals", "shadow_trades"}
    assert access.writes == {"shadow_trades", "system_state", "signals", "trades"}
    assert access.ddl == {"trades"}


def test_delete_from_is_a_write_only_and_on_conflict_update_is_not_a_table(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/user.py", '''
        async def f(db):
            await db.execute("DELETE FROM signals")
            await db.execute(
                "INSERT INTO system_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
    ''')
    access = access_of(repo, "app/user.py")
    assert access.reads == set()
    assert access.writes == {"signals", "system_state"}


def test_words_that_are_not_tables_and_docstrings_are_ignored(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/user.py", '''
        """Lee FROM trades y UPDATE signals: es prosa, no SQL."""

        def f(db):
            """Borra DELETE FROM signals (tambien prosa)."""
            return db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ''')
    access = access_of(repo, "app/user.py")
    assert (access.reads, access.writes, access.ddl) == (set(), set(), set())


def test_a_dynamic_table_name_counts_the_known_tables_named_in_the_function(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/user.py", '''
        async def clear(db):
            for table in ("trades", "signals", "not_a_table"):
                await db.execute(f"DELETE FROM {table}")
    ''')
    assert access_of(repo, "app/user.py").writes == {"trades", "signals"}


def test_a_module_gets_the_tables_of_the_repository_functions_it_calls_only(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/persistence/repositories/shadow_repo.py", '''
        async def get_open(db):
            return await db.fetch_all("SELECT * FROM shadow_trades")

        async def save(db):
            await _touch(db)
            await db.execute("INSERT INTO signals (id) VALUES (1)")

        async def _touch(db):
            await db.execute("UPDATE system_state SET value = 1")

        async def unrelated(db):
            await db.execute("DELETE FROM trades")
    ''')
    write(repo, "app/user.py", '''
        from app.persistence.repositories import shadow_repo
        from app.persistence.repositories.shadow_repo import save as save_signal

        async def f(db):
            await shadow_repo.get_open(db)
            await save_signal(db)
    ''')

    access = access_of(repo, "app/user.py")

    assert access.reads == {"shadow_trades"}
    assert access.writes == {"signals", "system_state"}          # `save` y lo que ella llama
    assert "trades" not in access.writes                         # `unrelated` no se llama


def test_repository_cycles_do_not_hang(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/persistence/repositories/loop_repo.py", '''
        async def a(db):
            await b(db)
            await db.execute("SELECT * FROM trades")

        async def b(db):
            await a(db)
            await db.execute("SELECT * FROM signals")
    ''')
    write(repo, "app/user.py", '''
        from app.persistence.repositories import loop_repo

        async def f(db):
            await loop_repo.a(db)
    ''')
    assert access_of(repo, "app/user.py").reads == {"trades", "signals"}


def test_methods_are_analysed_with_their_class_name(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/persistence/repositories/cls_repo.py", '''
        class Repo:
            async def get(self):
                return await self._rows()

            async def _rows(self):
                return await self._db.fetch_all("SELECT * FROM signals")
    ''')
    assert access_of(repo, "app/persistence/repositories/cls_repo.py").reads == {"signals"}


# --- pruebas -----------------------------------------------------------------------------------

def test_tests_that_import_or_cite_a_module_are_listed(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/risk.py", "X = 1\n")
    write(repo, "scripts/tool.py", "Y = 1\n")
    write(repo, "scripts/other.py", "Z = 1\n")
    write(repo, "tests/unit/test_risk.py", "from app import risk\n")
    write(repo, "tests/unit/test_tool.py", '''
        import subprocess
        from pathlib import Path

        def test_launch():
            subprocess.run(["python", str(Path("scripts") / "tool.py")])
    ''')
    write(repo, "tests/unit/test_docs.py", 'PATHS = ["app/risk.py"]\n')

    facts = facts_module.collect_facts(repo)

    assert facts["app/risk.py"].tests == ("tests/unit/test_docs.py", "tests/unit/test_risk.py")
    assert facts["scripts/tool.py"].tests == ("tests/unit/test_tool.py",)
    assert facts["scripts/other.py"].tests == ()


# --- grafo -------------------------------------------------------------------------------------

def graph_repo(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path)
    write(repo, "app/config.py", "X = 1\n")
    write(repo, "app/core/__init__.py", "")
    write(repo, "app/trading/__init__.py", "")
    for index in range(4):
        write(repo, f"app/core/c{index}.py", "from app.trading import t0\nfrom app import config\n")
        write(repo, f"app/trading/t{index}.py", "from app import config\n")
    write(repo, "scripts/s.py", "from app.trading import t1\n")
    return repo


def test_package_edges_count_imports_between_packages_only(tmp_path):
    facts = facts_module.collect_facts(graph_repo(tmp_path))
    edges = facts_module.package_edges(facts)
    assert edges[("core", "trading")] == 4
    assert edges[("core", "config")] == 4
    assert edges[("trading", "config")] == 4
    assert edges[("scripts", "trading")] == 1
    assert ("trading", "trading") not in edges


def test_the_diagram_hides_foundation_targets_assemblers_and_weak_edges(tmp_path):
    facts = facts_module.collect_facts(graph_repo(tmp_path))
    diagram = facts_module.render_mermaid(facts)
    assert diagram.startswith("```mermaid\nflowchart LR")
    assert "core -->|4| trading" in diagram
    assert "config" not in diagram           # destino de base: no se dibuja
    assert "scripts" not in diagram          # ensamblador: no se dibuja (y su flecha pesa 1)
    assert diagram.endswith("```")


def test_the_dependency_table_lists_every_package_with_its_sizes(tmp_path):
    facts = facts_module.collect_facts(graph_repo(tmp_path))
    table = facts_module.render_dependency_table(facts)
    assert "| `core` | 5 | " in table
    assert "trading 4" in table
    assert table.splitlines()[0].startswith("| Paquete |")


# --- presentacion ------------------------------------------------------------------------------

def test_long_lists_are_summarised_by_package_and_short_ones_listed():
    many = tuple(f"app.api.m{i}" for i in range(13))
    assert facts_module._names(many, "módulos") == "13 módulos (api 13)"
    assert facts_module._names(many[:3], "módulos") == "`app.api.m0`, `app.api.m1`, `app.api.m2`"
    assert facts_module._names((), "módulos") == "ninguno"
    unsummarised = facts_module._names(tuple(f"T{i}" for i in range(30)))  # configuracion y tablas
    assert unsummarised.count("`") == 60


def test_render_facts_has_one_line_per_fact_and_a_link_to_the_reference(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "app/x.py", "X = 1\n")
    text = facts_module.render_facts(facts_module.collect_facts(repo)["app/x.py"])
    assert "[`api/app/x.html`](../api/app/x.html)" in text
    assert text.count("\n- **") == 8


# --- frontend ----------------------------------------------------------------------------------

def test_frontend_facts_follow_relative_imports_endpoints_and_tests(tmp_path):
    write(tmp_path, "frontend/src/api.ts", '''
        export const a = () => apiFetch("/api/risk");
        export const b = () => apiFetch<X>("/health");
        export const c = () => fetch(`/api/memory/lessons`);
    ''')
    write(tmp_path, "frontend/src/components/Card.tsx", "export const Card = 1;\n")
    write(tmp_path, "frontend/src/RiskTab.tsx", '''
        import { Card } from "./components/Card";
        import { a } from "./api";
        import React from "react";
        export const RiskTab = 1;
    ''')
    write(tmp_path, "frontend/src/RiskTab.test.tsx", 'import { RiskTab } from "./RiskTab";\n')
    write(tmp_path, "frontend/src/components/Panel.tsx", 'import { a } from "../api";\n')

    facts = facts_module.collect_frontend_facts(tmp_path)

    assert "frontend/src/RiskTab.test.tsx" not in facts
    assert facts["frontend/src/RiskTab.tsx"].imports == (
        "frontend/src/api.ts", "frontend/src/components/Card.tsx",
    )
    assert facts["frontend/src/api.ts"].imported_by == (
        "frontend/src/RiskTab.tsx", "frontend/src/components/Panel.tsx",
    )
    assert facts["frontend/src/api.ts"].endpoints == ("/api/memory/lessons", "/api/risk", "/health")
    assert facts["frontend/src/RiskTab.tsx"].tests == ("frontend/src/RiskTab.test.tsx",)
