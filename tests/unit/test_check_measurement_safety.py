"""Pruebas de `scripts/check_measurement_safety.py`: avisa si un cambio toca una zona protegida.

Tres grupos:

1. Funciones puras (clasificar un cambio, leer defectos de configuracion, comparar el DDL de las
   tablas de la medicion).
2. El script de punta a punta contra repositorios de git temporales (el repositorio real no se
   toca): codigos de salida, modos `--staged` y `--since`, salida solo ASCII.
3. Alineacion con el repositorio real y con `docs/dev/ZONAS_PROTEGIDAS.md`: cada patron de la lista
   de zonas coincide con un archivo versionado, cada variable existe en `Settings`, los archivos
   fijados por hash son los que fija `test_prompt_v5_prepared.py` y el documento explica cada zona.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

from scripts import check_measurement_safety as cms
from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
ZONES_DOC = REPO_ROOT / "docs" / "dev" / "ZONAS_PROTEGIDAS.md"
SCRIPT = REPO_ROOT / "scripts" / "check_measurement_safety.py"

STRATEGY_BASE = '"""Doc."""\n\nFAST = 9\n\n\ndef signal(x):\n    """Doc."""\n    return x > FAST\n'

CONFIG_SAMPLE = '''"""Config."""
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    universe_size: int = Field(default=10, alias="UNIVERSE_SIZE")
    anthropic_sonnet_model: str = Field(default="claude-x", alias="ANTHROPIC_SONNET_MODEL")
    live_labeling: bool = Field(default=False, alias="LLM_LIVE_LABELING_ENABLED")
    max_drawdown_pct: float = Field(default=0.20, alias="MAX_DRAWDOWN_PCT")
    plain: int = 3
'''

SCHEMA_SAMPLE = '''"""Esquema de muestra."""
SCHEMA = """
-- Comentario que no es parte de la tabla.
CREATE TABLE IF NOT EXISTS shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    signal_group_key TEXT NOT NULL UNIQUE,
    llm_decision TEXT NOT NULL DEFAULT 'SIN_LLM'
        CHECK (llm_decision IN ('APROBADA', 'RECHAZADA', 'SIN_LLM')),
    UNIQUE (symbol, signal_group_key)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS llm_decision_inmutable
BEFORE UPDATE OF llm_decision ON shadow_trades
WHEN OLD.llm_decision <> 'SIN_LLM' AND NEW.llm_decision <> OLD.llm_decision
BEGIN
    SELECT RAISE(ABORT, 'llm_decision es inmutable una vez decidida');
END;
"""

_SHADOW_TRADES_MIGRATED_COLUMNS = {
    "reconciliation_failed": "INTEGER NOT NULL DEFAULT 0",
}
'''


def has_git() -> bool:
    try:
        done = subprocess.run(
            ["git", "--version"], capture_output=True, timeout=30, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


needs_git = pytest.mark.skipif(not has_git(), reason="git no esta disponible")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Prueba", "-c", "user.email=prueba@example.com",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
        creationflags=NO_WINDOW,
    )
    return done.stdout


def write(repo: Path, relative: str, text: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def make_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for relative, text in files.items():
        write(repo, relative, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


# ===== 1. funciones puras ========================================================================


def test_the_signature_ignores_docstrings_comments_and_spacing():
    changed = (
        '"""Otro texto."""\n\n# un comentario\nFAST = 9\n\n\n\ndef signal(x):\n'
        '    """Otro."""\n    return x > FAST   # nota\n'
    )

    assert cms.code_signature(STRATEGY_BASE) == cms.code_signature(changed)


def test_the_signature_sees_a_real_change():
    assert cms.code_signature(STRATEGY_BASE) != cms.code_signature(
        STRATEGY_BASE.replace("FAST = 9", "FAST = 10")
    )
    assert cms.code_signature(STRATEGY_BASE) != cms.code_signature(
        STRATEGY_BASE.replace("x > FAST", "x >= FAST")
    )


def test_an_empty_module_and_a_docstring_only_module_have_the_same_signature():
    assert cms.code_signature("") == cms.code_signature('"""Solo un docstring."""\n')


def test_a_docstring_only_function_equals_a_function_with_pass():
    assert cms.code_signature('def f():\n    """Doc."""\n') == cms.code_signature(
        "def f():\n    pass\n"
    )


@pytest.mark.parametrize(
    ("path", "old", "new", "pinned", "expected"),
    [
        ("a.py", None, None, False, None),
        ("a.py", None, "x = 1\n", False, cms.KIND_ADDED),
        ("a.py", "x = 1\n", None, False, cms.KIND_DELETED),
        ("a.py", "x = 1\n", "x = 1\n", False, None),
        ("a.py", "x = 1\r\ny = 2\r\n", "x = 1\ny = 2\n", False, None),
        ("a.py", "x = 1\n", "x = 2\n", False, cms.KIND_CODE),
        ("a.py", "x = 1\n", "x = 1  # nota\n", False, cms.KIND_DOCS),
        ("a.py", "x = 1\n", "x = 1  # nota\n", True, cms.KIND_PINNED),
        ("a.py", "x = 1\n", "x = (\n", False, cms.KIND_CODE),
        ("Dockerfile", "FROM a\n", "FROM b\n", False, cms.KIND_FILE),
    ],
)
def test_classify_change(path, old, new, pinned, expected):
    assert cms.classify_change(path, old, new, pinned=pinned) == expected


def test_ascii_fold_removes_accents_and_what_is_not_ascii():
    assert cms.ascii_fold("Agrupación de señales — 5 → 6") == "Agrupacion de senales  5  6"


@pytest.mark.parametrize(
    ("written", "default", "expected"),
    [
        ("10", "10", True),
        ("10", "10.0", True),
        ("0.20", "0.2", True),
        ("true", "True", True),
        ("false", "True", False),
        ("claude-x", "'claude-x'", True),
        ("claude-y", "'claude-x'", False),
        ("", "''", True),
        ("11", "10", False),
        ("abc", "10", False),
    ],
)
def test_same_value(written, default, expected):
    assert cms.same_value(written, default) is expected


def test_config_defaults_reads_alias_and_default_of_every_field():
    assert cms.config_defaults(CONFIG_SAMPLE) == {
        "UNIVERSE_SIZE": "10",
        "ANTHROPIC_SONNET_MODEL": "'claude-x'",
        "LLM_LIVE_LABELING_ENABLED": "False",
        "MAX_DRAWDOWN_PCT": "0.2",
        "PLAIN": "3",
    }


def test_config_defaults_is_empty_for_code_that_does_not_parse_or_has_no_settings():
    assert cms.config_defaults("def (") == {}
    assert cms.config_defaults("x = 1\n") == {}


def test_config_defaults_of_the_real_settings_include_the_protected_variables():
    defaults = cms.config_defaults((REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8"))

    assert defaults["LLM_LIVE_LABELING_ENABLED"] == "False"
    assert defaults["ANTHROPIC_SONNET_MODEL"] == "'claude-sonnet-5-5'"
    assert defaults["LIVE_SL_MARGIN_CAP_PCT"] == "50.0"


def test_carrier_values_only_match_whole_names_and_skip_comments_and_powershell_help():
    text = (
        "<#\n  LLM_MAX_TOKENS=999 (ayuda)\n#>\n"
        "# LLM_MAX_TOKENS=1\n"
        "LLM_MAX_TOKENS=300\n"
        "LLM_MAX_TOKENS_EXTRA=5\n"
        "OTHER_LLM_MAX_TOKENS=7\n"
    )

    found = cms.carrier_values(text, frozenset({"LLM_MAX_TOKENS"}))

    assert found == {"LLM_MAX_TOKENS": ["LLM_MAX_TOKENS=300"]}


def test_config_changes_in_the_settings_file_compare_the_defaults():
    new = CONFIG_SAMPLE.replace("default=10,", "default=12,")
    new = new.replace("plain: int = 3", "plain: int = 4")

    changes = cms.config_changes(
        cms.CONFIG_FILE, CONFIG_SAMPLE, new, frozenset({"UNIVERSE_SIZE", "MAX_DRAWDOWN_PCT"}),
    )

    assert changes == [("UNIVERSE_SIZE", "10", "12")]


def test_config_changes_in_a_carrier_compare_the_lines_that_name_the_variable():
    changes = cms.config_changes(
        "docker-compose.yml", "A: 1\nUNIVERSE_SIZE: 10\n", "A: 2\nUNIVERSE_SIZE: 12\n",
        frozenset({"UNIVERSE_SIZE"}),
    )

    assert changes == [("UNIVERSE_SIZE", "UNIVERSE_SIZE: 10", "UNIVERSE_SIZE: 12")]


def test_every_zone_title_and_id_is_unique_and_every_level_is_known():
    ids = [zone.id for zone in cms.ZONES]
    titles = [zone.title for zone in cms.ZONES]

    assert len(set(ids)) == len(ids)
    assert len(set(titles)) == len(titles)
    assert {zone.level for zone in cms.ZONES} <= {cms.LEVEL_PROTECTED, cms.LEVEL_REVIEW}


def test_a_variable_belongs_to_a_single_zone():
    names = [name for zone in cms.ZONES for name in zone.config]

    assert len(set(names)) == len(names)


def test_zones_for_returns_every_matching_zone():
    titles = {zone.title for zone in cms.zones_for("app/trading/signal_generator.py")}

    assert titles == {"Agrupación de señales y ciclo del scheduler", "Tope de SL"}
    assert cms.zones_for("frontend/src/App.tsx") == []


# ----- evaluate_file ---------------------------------------------------------------------------


def test_a_code_change_in_a_protected_file_is_protected():
    findings = cms.evaluate_file(
        "app/strategies/ema_cross.py", STRATEGY_BASE, STRATEGY_BASE.replace("= 9", "= 10"),
    )

    assert [(f.level, f.kind) for f in findings] == [(cms.LEVEL_PROTECTED, cms.KIND_CODE)]
    assert findings[0].blocks is True


def test_a_docstring_only_change_does_not_block():
    findings = cms.evaluate_file(
        "app/strategies/ema_cross.py", STRATEGY_BASE, STRATEGY_BASE.replace("Doc.", "Otro."),
    )

    assert [f.kind for f in findings] == [cms.KIND_DOCS]
    assert findings[0].blocks is False


def test_any_change_to_a_hash_pinned_file_is_flagged_even_a_comment():
    findings = cms.evaluate_file("app/llm/prompts.py", "X = 1\n", "X = 1  # nota\n")

    assert [(f.level, f.kind) for f in findings] == [(cms.LEVEL_PROTECTED, cms.KIND_PINNED)]


def test_a_file_in_two_zones_is_reported_once_with_both_titles_and_the_stricter_level():
    findings = cms.evaluate_file("app/api/routes/control.py", "x = 1\n", "x = 2\n")

    assert len(findings) == 1
    assert findings[0].level == cms.LEVEL_REVIEW
    assert "Canales" in findings[0].zone
    assert "Controles" in findings[0].zone


def test_a_file_outside_every_zone_has_no_findings():
    assert cms.evaluate_file("frontend/src/App.tsx", "a", "b") == []
    assert cms.evaluate_file("docs/README.md", "a", "b") == []


def test_creating_the_official_verdict_record_by_hand_is_flagged():
    path = "docs/VEREDICTO_3_6_REGISTRO_2026-12-05.txt"

    findings = cms.evaluate_file(path, None, "Veredicto: APORTA_VALOR\n")

    assert [(f.level, f.kind) for f in findings] == [(cms.LEVEL_PROTECTED, cms.KIND_RECORD)]
    assert cms.evaluate_file(path, "x", "x") == []


def test_the_placebo_record_is_not_the_one_that_opens_the_gate():
    assert cms.is_verdict_record("docs/PLACEBO_2026-12-05.txt") is False
    assert cms.evaluate_file("docs/PLACEBO_2026-12-05.txt", None, "x") == []


# ----- parametros de configuracion --------------------------------------------------------------


def test_a_changed_default_in_the_settings_file_is_protected_with_before_and_after():
    new = CONFIG_SAMPLE.replace('default="claude-x"', 'default="claude-y"')

    findings = cms.evaluate_file(cms.CONFIG_FILE, CONFIG_SAMPLE, new)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == cms.LEVEL_PROTECTED
    assert finding.kind == cms.KIND_CONFIG
    assert finding.path == "app/config.py: ANTHROPIC_SONNET_MODEL"
    assert "'claude-x'" in finding.detail
    assert "'claude-y'" in finding.detail


def test_a_changed_default_of_a_review_variable_is_only_to_review():
    new = CONFIG_SAMPLE.replace("default=0.20", "default=0.30")

    findings = cms.evaluate_file(cms.CONFIG_FILE, CONFIG_SAMPLE, new)

    assert [(f.level, f.path) for f in findings] == [
        (cms.LEVEL_REVIEW, "app/config.py: MAX_DRAWDOWN_PCT")
    ]


def test_a_change_to_a_setting_that_is_not_watched_is_ignored():
    assert cms.evaluate_file(
        cms.CONFIG_FILE, CONFIG_SAMPLE, CONFIG_SAMPLE.replace("plain: int = 3", "plain: int = 4"),
    ) == []


def test_the_env_example_is_only_flagged_when_it_stops_matching_the_default():
    defaults = cms.config_defaults(CONFIG_SAMPLE)

    same = cms.evaluate_file(".env.example", None, "UNIVERSE_SIZE=10\n", defaults)
    different = cms.evaluate_file(
        ".env.example", "UNIVERSE_SIZE=10\n", "UNIVERSE_SIZE=20\n", defaults,
    )
    removed = cms.evaluate_file(".env.example", "UNIVERSE_SIZE=10\n", "", defaults)

    assert same == []
    assert removed == []
    assert [(f.level, f.kind) for f in different] == [(cms.LEVEL_PROTECTED, cms.KIND_CONFIG)]
    assert "10" in different[0].detail
    assert "20" in different[0].detail


def test_a_changed_line_in_compose_is_protected_and_a_new_line_is_only_to_review():
    changed = cms.evaluate_file(
        "docker-compose.yml", "UNIVERSE_SIZE: 10\n", "UNIVERSE_SIZE: 20\n",
    )
    introduced = cms.evaluate_file("docker-compose.yml", "A: 1\n", "A: 1\nUNIVERSE_SIZE: 20\n")

    assert [(f.level, f.kind) for f in changed] == [(cms.LEVEL_PROTECTED, cms.KIND_CONFIG)]
    assert [(f.level, f.kind) for f in introduced] == [(cms.LEVEL_REVIEW, cms.KIND_CONFIG_NEW)]


def test_the_help_block_of_the_start_script_does_not_count_as_a_setting():
    old = "<#\n  LLM_LIVE_LABELING_ENABLED=true (ayuda)\n#>\n$x = 1\n"
    new = "<#\n  LLM_LIVE_LABELING_ENABLED=true (ayuda, texto nuevo)\n#>\n$x = 1\n"

    assert cms.evaluate_file("scripts/start_bot.ps1", old, new) == []


# ----- esquema -----------------------------------------------------------------------------------


def test_the_schema_is_read_as_columns_constraints_trigger_and_migrated_columns():
    schema = cms.protected_schema(SCHEMA_SAMPLE)

    assert set(schema) == {"shadow_trades", "disparador llm_decision_inmutable"}
    columns = schema["shadow_trades"]
    assert columns["symbol"] == "TEXT NOT NULL"
    assert columns["side"] == "TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT'))"
    assert columns["llm_decision"].endswith(
        "CHECK (llm_decision IN ('APROBADA', 'RECHAZADA', 'SIN_LLM'))"
    )
    assert columns["@UNIQUE (symbol, signal_group_key)"] == ""
    assert columns["reconciliation_failed"] == "INTEGER NOT NULL DEFAULT 0"
    assert "trades" not in schema


def test_no_schema_change_when_only_comments_or_other_tables_change():
    new = SCHEMA_SAMPLE.replace("-- Comentario que no es parte de la tabla.", "-- Otro comentario.")
    new = new.replace("symbol TEXT NOT NULL\n);", "symbol TEXT NOT NULL,\n    note TEXT\n);")

    assert cms.schema_changes(SCHEMA_SAMPLE, new) == []


def test_a_new_column_is_additive_and_a_changed_or_removed_one_is_not():
    added = SCHEMA_SAMPLE.replace(
        "    symbol TEXT NOT NULL,\n    side",
        "    symbol TEXT NOT NULL,\n    note TEXT,\n    side", 1,
    )
    changed = SCHEMA_SAMPLE.replace("symbol TEXT NOT NULL,\n    side", "symbol TEXT,\n    side", 1)
    removed = SCHEMA_SAMPLE.replace("    symbol TEXT NOT NULL,\n", "", 1)

    assert cms.schema_changes(SCHEMA_SAMPLE, added) == [(True, "shadow_trades.note")]
    assert cms.schema_changes(SCHEMA_SAMPLE, changed) == [
        (False, "shadow_trades.symbol: definicion cambiada")
    ]
    assert cms.schema_changes(SCHEMA_SAMPLE, removed) == [
        (False, "shadow_trades.symbol: columna eliminada")
    ]


def test_a_new_migrated_column_is_additive():
    new = SCHEMA_SAMPLE.replace(
        '"reconciliation_failed": "INTEGER NOT NULL DEFAULT 0",',
        '"reconciliation_failed": "INTEGER NOT NULL DEFAULT 0",\n    "extra": "TEXT",',
    )

    assert cms.schema_changes(SCHEMA_SAMPLE, new) == [(True, "shadow_trades.extra")]


def test_a_changed_constraint_or_trigger_is_not_additive():
    constraint = SCHEMA_SAMPLE.replace("UNIQUE (symbol, signal_group_key)", "UNIQUE (symbol)")
    trigger = SCHEMA_SAMPLE.replace("es inmutable una vez decidida", "ya no es inmutable")

    assert all(not additive for additive, _ in cms.schema_changes(SCHEMA_SAMPLE, constraint))
    assert cms.schema_changes(SCHEMA_SAMPLE, constraint)
    assert cms.schema_changes(SCHEMA_SAMPLE, trigger) == [
        (False, "disparador llm_decision_inmutable: restriccion o cuerpo cambiado")
    ]


def test_evaluate_file_splits_schema_changes_into_protected_and_additive():
    both = SCHEMA_SAMPLE.replace(
        "symbol TEXT NOT NULL,\n    side", "symbol TEXT,\n    note TEXT,\n    side", 1,
    )

    findings = cms.evaluate_file(cms.SCHEMA_FILE, SCHEMA_SAMPLE, both)

    assert [(f.level, f.kind) for f in findings] == [
        (cms.LEVEL_PROTECTED, cms.KIND_SCHEMA), (cms.LEVEL_REVIEW, cms.KIND_SCHEMA_ADDED),
    ]


def test_the_real_schema_is_read_for_every_watched_table_and_the_trigger():
    text = (REPO_ROOT / "app" / "persistence" / "database.py").read_text(encoding="utf-8")

    schema = cms.protected_schema(text)

    assert set(cms.PROTECTED_TABLES) <= set(schema)
    assert "disparador llm_decision_inmutable" in schema
    assert "llm_decision" in schema["shadow_trades"]
    assert "reconciliation_failed" in schema["shadow_trades"]
    assert "features_version" in schema["llm_logs"]
    assert cms.schema_changes(text, text) == []


# ===== 2. el script contra repositorios de git temporales ========================================


@pytest.fixture
def repo(tmp_path):
    return make_repo(
        tmp_path,
        {
            "app/strategies/ema_cross.py": STRATEGY_BASE,
            "app/telegram/commands.py": "X = 1\n",
            "app/other.py": "Y = 1\n",
            "docs/README.md": "# Documentos\n",
        },
    )


@needs_git
def test_a_clean_tree_exits_zero(repo, capsys):
    code = cms.main([], repo=repo)

    assert code == 0
    assert "Sin cambios en zonas protegidas." in capsys.readouterr().out


@needs_git
def test_a_code_change_in_a_protected_file_exits_one_and_names_the_file(repo, capsys):
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("= 9", "= 10"))

    code = cms.main([], repo=repo)

    out = capsys.readouterr().out
    assert code == 1
    assert "app/strategies/ema_cross.py" in out
    assert "[codigo cambiado]" in out
    assert "PROTEGIDO" in out
    assert cms.WHAT_TO_DO in out


@needs_git
def test_a_docstring_only_change_exits_zero_and_is_listed_as_documentation(repo, capsys):
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("Doc.", "Texto nuevo."))

    code = cms.main([], repo=repo)

    out = capsys.readouterr().out
    assert code == 0
    assert "Solo documentacion" in out
    assert "app/strategies/ema_cross.py" in out
    assert "PROTEGIDO --" not in out


@needs_git
def test_a_change_outside_the_zones_is_not_reported(repo, capsys):
    write(repo, "app/other.py", "Y = 2\n")
    write(repo, "docs/README.md", "# Otros documentos\n")

    assert cms.main([], repo=repo) == 0
    assert "Sin cambios en zonas protegidas." in capsys.readouterr().out


@needs_git
def test_a_new_untracked_file_in_a_protected_zone_is_flagged(repo, capsys):
    write(repo, "app/strategies/nueva.py", "SEPTIMA = 1\n")

    code = cms.main([], repo=repo)

    assert code == 1
    assert "app/strategies/nueva.py  [archivo nuevo]" in capsys.readouterr().out


@needs_git
def test_a_deleted_protected_file_is_flagged(repo, capsys):
    (repo / "app/strategies/ema_cross.py").unlink()

    assert cms.main([], repo=repo) == 1
    assert "[archivo borrado]" in capsys.readouterr().out


@needs_git
def test_staged_mode_only_sees_what_would_be_committed(repo, capsys):
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("= 9", "= 10"))

    assert cms.main(["--staged"], repo=repo) == 0
    assert "Sin cambios en zonas protegidas." in capsys.readouterr().out

    git(repo, "add", "app/strategies/ema_cross.py")

    assert cms.main(["--staged"], repo=repo) == 1
    assert "app/strategies/ema_cross.py" in capsys.readouterr().out


@needs_git
def test_staged_mode_ignores_a_later_unstaged_edit(repo, capsys):
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("Doc.", "Texto nuevo."))
    git(repo, "add", "app/strategies/ema_cross.py")
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("= 9", "= 10"))

    assert cms.main(["--staged"], repo=repo) == 0
    assert "Solo documentacion" in capsys.readouterr().out


@needs_git
def test_since_mode_sees_committed_changes_and_names_the_commits(repo, capsys):
    base = git(repo, "rev-parse", "HEAD").strip()
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("= 9", "= 10"))
    git(repo, "commit", "-q", "-am", "cambia la estrategia")

    code = cms.main(["--since", base], repo=repo)

    out = capsys.readouterr().out
    assert code == 1
    assert "Commits que tocaron archivos protegidos:" in out
    assert "cambia la estrategia" in out
    assert cms.main([], repo=repo) == 0  # contra HEAD ya no hay nada sin confirmar


@needs_git
def test_an_unknown_revision_exits_two(repo, capsys):
    code = cms.main(["--since", "no-existe-esta-revision"], repo=repo)

    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR" in captured.err


@needs_git
def test_review_zones_only_fail_with_strict(repo, capsys):
    write(repo, "app/telegram/commands.py", "X = 2\n")

    assert cms.main([], repo=repo) == 0
    assert "PARA REVISAR" in capsys.readouterr().out
    assert cms.main(["--strict"], repo=repo) == 1


@needs_git
def test_protected_findings_come_before_the_ones_to_review(repo, capsys):
    write(repo, "app/telegram/commands.py", "X = 2\n")
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("= 9", "= 10"))

    cms.main([], repo=repo)

    out = capsys.readouterr().out
    assert out.index("PROTEGIDO --") < out.index("PARA REVISAR")


@needs_git
def test_the_report_is_plain_ascii_even_with_accented_zone_names(repo, capsys):
    write(repo, "app/strategies/ema_cross.py", STRATEGY_BASE.replace("= 9", "= 10"))

    cms.main([], repo=repo)

    out = capsys.readouterr().out
    assert out.isascii()
    assert "Estrategias y parametros" in out


@needs_git
def test_a_hand_made_verdict_record_is_flagged(repo, capsys):
    write(repo, "docs/VEREDICTO_3_6_REGISTRO_2026-12-05.txt", "Veredicto: APORTA_VALOR\n")

    assert cms.main([], repo=repo) == 1
    assert "[registro oficial del veredicto creado a mano]" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["--since", "HEAD", "--since-measurement"],
    ["--staged", "--since", "HEAD"],
    ["--staged", "--since-measurement"],
])
def test_the_options_are_mutually_exclusive_where_they_must_be(argv):
    with pytest.raises(SystemExit) as raised:
        cms.main(argv, repo=REPO_ROOT)

    assert raised.value.code == 2


def test_list_prints_every_zone_and_exits_zero(capsys):
    code = cms.main(["--list"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.isascii()
    for zone in cms.ZONES:
        assert cms.ascii_fold(zone.title) in out
    assert cms.VERDICT_RECORD_PATTERN in out
    assert "(fijado por hash)" in out


# ===== 3. alineacion con el repositorio real y con el documento ==================================


def tracked_files() -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=120,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git no esta disponible o esto no es un repositorio")
    return [line.replace("\\", "/") for line in listed.stdout.splitlines() if line]


def test_every_pattern_of_every_zone_matches_a_versioned_file():
    files = tracked_files()
    unmatched = [
        f"{zone.id}: {pattern}"
        for zone in cms.ZONES for pattern in zone.files
        if not any(fnmatch.fnmatchcase(f, pattern) for f in files)
    ]

    assert unmatched == [], "patrones de la lista de zonas que ya no existen: " + str(unmatched)


def test_every_pinned_file_is_listed_in_its_zone():
    for zone in cms.ZONES:
        assert set(zone.pinned) <= set(zone.files), zone.id


def test_the_pinned_files_are_the_ones_the_hash_test_pins():
    source = (REPO_ROOT / "tests" / "unit" / "test_prompt_v5_prepared.py").read_text(
        encoding="utf-8"
    )
    pinned_by_the_test = set(re.findall(r'\("(app/llm/[a-z_0-9]+\.py)", V4_', source))
    pinned_here = {path for zone in cms.ZONES for path in zone.pinned}

    assert pinned_by_the_test == pinned_here == {"app/llm/prompts.py", "app/llm/market_features.py"}


def test_every_variable_of_a_zone_is_a_real_setting_and_is_in_the_env_example():
    defaults = cms.config_defaults((REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8"))
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    declared = {line.split("=", 1)[0].strip() for line in example.splitlines() if "=" in line}
    names = [name for zone in cms.ZONES for name in zone.config]

    assert [n for n in names if n not in defaults] == []
    assert [n for n in names if n not in declared] == []


def test_the_env_example_agrees_with_the_defaults_of_the_protected_variables():
    defaults = cms.config_defaults((REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8"))
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    mismatched = []
    for name in cms.config_owners():
        found = cms.carrier_values(example, frozenset({name})).get(name)
        if found and not cms.same_value(found[-1].partition("=")[2], defaults[name]):
            mismatched.append((name, found[-1], defaults[name]))

    assert mismatched == []


def test_the_measurement_start_is_an_ancestor_of_head():
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", cms.MEASUREMENT_START, "HEAD"],
        capture_output=True, timeout=60, creationflags=NO_WINDOW,
    )
    if done.returncode not in (0, 1):
        pytest.skip("el commit del inicio de la medicion no esta en este clon")

    assert done.returncode == 0


def test_the_script_never_imports_app_and_never_writes():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }

    assert not [m for m in imported if m == "app" or m.startswith("app.")]
    assert calls.isdisjoint({
        "write_text", "write_bytes", "unlink", "rmdir", "mkdir", "touch", "rename", "open",
        "remove", "system", "Popen",
    })


def sections_of(text: str) -> dict[str, str]:
    """Titulo de cada encabezado -> su texto, hasta el siguiente de su nivel o de uno menor."""
    headings = [(m.start(), len(m.group(1)), m.group(2).strip())
                for m in re.finditer(r"^(#{2,4}) (.+)$", text, flags=re.MULTILINE)]
    found = {}
    for index, (start, level, title) in enumerate(headings):
        end = next((s for s, lv, _ in headings[index + 1:] if lv <= level), len(text))
        found[title] = text[start:end]
    return found


def test_the_protected_zones_document_explains_every_zone_with_its_files_and_variables():
    text = ZONES_DOC.read_text(encoding="utf-8")
    sections = sections_of(text)
    problems = []
    for zone in cms.ZONES:
        section = sections.get(zone.title)
        if section is None:
            problems.append(f"falta la seccion '{zone.title}'")
            continue
        problems += [
            f"'{zone.title}' no cita `{item}`"
            for item in (*zone.files, *zone.config) if f"`{item}`" not in section
        ]
    record = sections.get("Gate, placebo y veredicto", "")
    if f"`{cms.VERDICT_RECORD_PATTERN}`" not in record:
        problems.append(f"'Gate, placebo y veredicto' no cita `{cms.VERDICT_RECORD_PATTERN}`")

    assert problems == []


def protected_variable_rows() -> dict[str, tuple[str, str]]:
    """Fila de la tabla de variables protegidas del documento: nombre -> (defecto, zona)."""
    section = sections_of(ZONES_DOC.read_text(encoding="utf-8"))[
        "Parámetros de configuración protegidos"
    ]
    rows = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and re.fullmatch(r"`[A-Z0-9_]+`", cells[0]):
            default = cells[1].strip("`")
            rows[cells[0].strip("`")] = ("" if default == "(vacío)" else default, cells[2])
    return rows


def test_the_table_of_protected_variables_lists_exactly_the_protected_ones():
    expected = {
        name for zone in cms.ZONES if zone.level == cms.LEVEL_PROTECTED for name in zone.config
    }

    assert set(protected_variable_rows()) == expected


def test_the_table_of_protected_variables_agrees_with_the_settings_defaults_and_zones():
    defaults = cms.config_defaults((REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8"))
    owners = cms.config_owners()
    problems = []
    for name, (written, zone) in protected_variable_rows().items():
        if not cms.same_value(written, defaults[name]):
            problems.append(f"{name}: dice {written!r} y el defecto es {defaults[name]}")
        if zone != owners[name].title:
            problems.append(f"{name}: dice la zona {zone!r} y es {owners[name].title!r}")

    assert problems == []


def test_the_document_says_where_each_level_is_and_how_to_run_the_script():
    text = ZONES_DOC.read_text(encoding="utf-8")

    assert "scripts/check_measurement_safety.py" in text
    assert "--since-measurement" in text
    assert cms.MEASUREMENT_START in text
    assert "Si de verdad hay que cambiar algo protegido" in sections_of(text)
