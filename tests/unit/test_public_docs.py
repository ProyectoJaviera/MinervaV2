"""La documentacion publica no se queda atras del codigo ni promete lo que este no hace.

Lo que vigila (todo sin red y sin leer `.env`):

- Los enlaces relativos y sus anclas (`archivo.md#seccion`) resuelven, con el algoritmo de anclas
  de GitHub.
- Las rutas del repositorio que citan los documentos existen.
- Cada variable de `.env.example` esta descrita en `docs/CONFIGURACION.md`, no hay variables
  inventadas y los valores por defecto de las tablas coinciden con los de `app/config.py`.
- Los scripts y las opciones (`--flag`, `-Switch`) que citan los documentos existen de verdad.
- Los comandos de Telegram, las pestanas del dashboard, las rutas de la API y las variables de
  Docker Compose que describen los documentos son las del codigo.
- Ningun archivo versionado lleva rutas personales, nombres propios (salvo el titular de la
  licencia) ni correos.
- Los indices de `docs/` y de `docs/historial/` listan todos sus documentos; existen la licencia,
  la guia de contribucion, el registro de cambios y las plantillas de GitHub.
- Los bloques de codigo estan balanceados y las tablas tienen un numero de columnas coherente.

Cada comprobador tiene su propia prueba con un caso que debe fallar (un comprobador que nunca
encuentra nada no vigila nada). Nunca imprime el valor de una credencial: solo rutas y nombres.
"""

from __future__ import annotations

import functools
import re
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
HISTORIAL = DOCS / "historial"

# Documentos de cara al publico (guias, conceptos, referencia y estandar de GitHub).
ROOT_DOCS = ("README.md", "CONTRIBUTING.md", "CHANGELOG.md")
TOP_LEVEL_DOCS = sorted(p.name for p in DOCS.glob("*.md"))
GITHUB_DOCS = sorted(
    p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / ".github").rglob("*.md")
)
# La documentacion tecnica para quien modifica el codigo (docs/dev/ y sus guias por paquete) pasa
# por TODAS las comprobaciones de abajo; su contenido propio lo vigila `test_dev_docs.py`.
DEV_DOCS = sorted(
    p.relative_to(REPO_ROOT).as_posix()
    for pattern in ("dev/*.md", "dev/modulos/*.md") for p in DOCS.glob(pattern)
)
PUBLIC_DOCS = [
    *ROOT_DOCS, *(f"docs/{name}" for name in TOP_LEVEL_DOCS), "docs/historial/README.md",
    *GITHUB_DOCS, *DEV_DOCS,
]
HISTORIAL_DOCS = sorted(
    p.relative_to(REPO_ROOT).as_posix() for p in HISTORIAL.glob("*.md") if p.name != "README.md"
)
ALL_DOCS = [*PUBLIC_DOCS, *HISTORIAL_DOCS]

FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"(?<!\\)\[(?:[^\]\n]|\\\])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def strip_fenced_code(text: str) -> str:
    """El texto sin los bloques de codigo (``` o ~~~): ahi dentro no hay enlaces ni encabezados."""
    kept, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def github_slug(heading: str) -> str:
    """El ancla que GitHub genera para un encabezado: minusculas, sin signos de puntuacion (se
    conservan letras de cualquier alfabeto, digitos, guiones y guiones bajos) y cada espacio es un
    guion."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # [texto](url) -> texto
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


@functools.cache
def anchors_of(relative: str) -> frozenset[str]:
    """Todas las anclas de un documento (con el sufijo -1, -2... de los encabezados repetidos)."""
    seen: dict[str, int] = {}
    anchors = set()
    for line in strip_fenced_code(read(relative)).splitlines():
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not match:
            continue
        slug = github_slug(match.group(1))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return frozenset(anchors)


def links_in(text: str) -> list[str]:
    return LINK.findall(INLINE_CODE.sub("", strip_fenced_code(text)))


def broken_links(relative: str, text: str) -> list[str]:
    """Los enlaces relativos de `text` (el contenido del documento `relative`) que no resuelven:
    el archivo no existe o la ancla (`#seccion`) no es la de ningun encabezado del destino."""
    base = (REPO_ROOT / relative).parent
    broken = []
    for target in links_in(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path_part, _, fragment = target.partition("#")
        fragment = urllib.parse.unquote(fragment)
        if not path_part:
            target_file = REPO_ROOT / relative
        else:
            target_file = (base / urllib.parse.unquote(path_part)).resolve()
            if not target_file.exists():
                broken.append(f"{target} (no existe)")
                continue
        if fragment and target_file.suffix == ".md":
            try:
                known = anchors_of(target_file.relative_to(REPO_ROOT).as_posix())
            except ValueError:
                broken.append(f"{target} (apunta fuera del repositorio)")
                continue
            if fragment not in known:
                broken.append(f"{target} (la ancla no existe)")
    return broken


# --- enlaces y anclas ----------------------------------------------------------------------------


@pytest.mark.parametrize("relative", ALL_DOCS)
def test_internal_links_and_anchors_resolve(relative):
    broken = broken_links(relative, read(relative))

    assert not broken, f"enlaces rotos en {relative}: {broken}"


def test_the_anchor_algorithm_matches_githubs_on_known_headings():
    assert github_slug("3.4 `JWT_SECRET`") == "34-jwt_secret"
    assert github_slug("Qué esperar los próximos días") == "qué-esperar-los-próximos-días"
    assert github_slug("Claude (Anthropic)") == "claude-anthropic"
    assert github_slug("Lo no verificado") == "lo-no-verificado"


def test_the_link_checker_catches_a_missing_file_a_missing_anchor_and_ignores_code():
    text = (
        "[ok](GUIA_INSTALACION.md#antes-de-empezar) [falta](NO_EXISTE.md) "
        "[mal ancla](USO.md#ancla-que-no-existe) [externo](https://example.com/x) "
        "`[en codigo](tampoco.md)`\n```\n[bloque](tampoco.md)\n```\n"
    )

    broken = broken_links("docs/README.md", text)

    assert broken == [
        "NO_EXISTE.md (no existe)", "USO.md#ancla-que-no-existe (la ancla no existe)",
    ]


# Rutas que citan los documentos y que no estan versionadas: se generan al compilar o al operar.
GENERATED_PATHS = {"frontend/dist", "frontend/dist/"}
CITED_PATH = re.compile(
    r"`((?:app|scripts|docs|tests|docker|frontend|\.github)/[A-Za-z0-9_./*<>{}\-]*)`"
)
TEMPLATE_HINTS = ("<", ">", "*", "{", "AAAAMMDD", "_fecha")
# El indice del historial cita a proposito las rutas ANTIGUAS (y una de ejemplo que ya no existe)
# para decir donde esta cada archivo ahora.
OLD_PATHS_ON_PURPOSE = {"docs/historial/README.md"}


def missing_cited_paths(text: str) -> list[str]:
    """Las rutas del repositorio citadas entre comillas invertidas (`app/x.py`) que no existen."""
    missing = []
    for cited in sorted(set(CITED_PATH.findall(strip_fenced_code(text)))):
        if any(hint in cited for hint in TEMPLATE_HINTS) or cited in GENERATED_PATHS:
            continue
        if not (REPO_ROOT / cited).exists():
            missing.append(cited)
    return missing


@pytest.mark.parametrize("relative", [d for d in PUBLIC_DOCS if d not in OLD_PATHS_ON_PURPOSE])
def test_cited_repository_paths_exist(relative):
    missing = missing_cited_paths(read(relative))

    assert not missing, f"{relative} cita rutas que no existen: {missing}"


def test_the_path_checker_catches_a_missing_path_and_skips_templates():
    text = (
        "`app/main.py` `app/no_existe.py` `docs/VEREDICTO_3_6_REGISTRO*.txt` `frontend/dist` "
        "`docs/x_<fecha>.txt`"
    )

    assert missing_cited_paths(text) == ["app/no_existe.py"]


# --- bloques de codigo y tablas ------------------------------------------------------------------


@pytest.mark.parametrize("relative", ALL_DOCS)
def test_code_fences_are_balanced(relative):
    fences = [line for line in read(relative).splitlines() if FENCE.match(line)]

    assert len(fences) % 2 == 0, f"{relative}: bloque de codigo sin cerrar"


def split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return re.split(r"(?<!\\)\|", body)


def table_problems(text: str) -> list[str]:
    """Tablas cuyas filas no tienen todas el mismo numero de columnas (un `|` sin escapar dentro
    de una celda rompe la tabla en GitHub, incluso dentro de un trozo `de codigo`)."""
    problems = []
    table: list[tuple[int, int]] = []  # (numero de linea, columnas)
    in_fence = False

    def close(rows):
        if len(rows) >= 2 and len({cols for _, cols in rows}) != 1:
            problems.append(f"linea {rows[0][0]}: columnas {[cols for _, cols in rows]}")

    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            close(table)
            table = []
            continue
        if not in_fence and line.strip().startswith("|"):
            table.append((number, len(split_row(line))))
        else:
            close(table)
            table = []
    close(table)
    return problems


@pytest.mark.parametrize("relative", ALL_DOCS)
def test_markdown_tables_have_a_consistent_number_of_columns(relative):
    problems = table_problems(read(relative))

    assert not problems, f"{relative}: tablas con filas de distinto ancho: {problems}"


def test_the_table_checker_catches_a_stray_pipe_inside_a_cell():
    good = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    bad = "| a | b |\n|---|---|\n| `x|y` | 2 |\n"
    escaped = "| a | b |\n|---|---|\n| `x\\|y` | 2 |\n"

    assert table_problems(good) == [] and table_problems(escaped) == []
    assert len(table_problems(bad)) == 1


def test_mermaid_diagrams_declare_a_known_type():
    known = ("flowchart", "graph", "sequenceDiagram", "erDiagram", "classDiagram", "stateDiagram")
    seen = 0
    for relative in PUBLIC_DOCS:
        lines = read(relative).splitlines()
        for index, line in enumerate(lines):
            if line.strip() == "```mermaid":
                seen += 1
                first = lines[index + 1].strip()
                assert first.startswith(known), f"{relative}: diagrama mermaid sin tipo: {first!r}"

    assert seen >= 3, "los documentos ya no traen sus diagramas"


# --- configuracion: .env.example <-> docs/CONFIGURACION.md <-> app/config.py ---------------------


def env_example_variables() -> list[str]:
    names = []
    for line in read(".env.example").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            names.append(match.group(1))
    return names


def configuration_rows(text: str | None = None) -> dict[str, list[str]]:
    """nombre de variable -> celdas de su fila, de las tablas de docs/CONFIGURACION.md."""
    rows: dict[str, list[str]] = {}
    for line in (text if text is not None else read("docs/CONFIGURACION.md")).splitlines():
        match = re.match(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|", line)
        if match:
            name = match.group(1)
            assert name not in rows, f"{name} esta descrita dos veces en CONFIGURACION.md"
            rows[name] = [cell.strip() for cell in split_row(line)]
    return rows


COMPOSE_VARIABLE = re.compile(r"\$\{(MINERVA_[A-Z_]+)(?::-[^}]*)?\}")


def compose_variables() -> set[str]:
    return set(COMPOSE_VARIABLE.findall(read("docker-compose.yml")))


def test_every_env_example_variable_is_described_in_the_configuration_guide():
    documented = set(configuration_rows())
    missing = [name for name in env_example_variables() if name not in documented]

    assert not missing, f"variables de .env.example sin describir en CONFIGURACION.md: {missing}"


def test_the_configuration_guide_describes_no_invented_variables():
    known = set(env_example_variables()) | compose_variables()
    invented = [name for name in configuration_rows() if name not in known]

    assert not invented, f"CONFIGURACION.md describe variables que no existen: {invented}"


def test_every_docker_compose_variable_is_documented():
    documented = set(configuration_rows())

    assert compose_variables() <= documented, compose_variables() - documented


def test_the_configuration_checker_rejects_a_variable_described_twice():
    row = "| `LEVERAGE` | x | `10` | y |\n"

    with pytest.raises(AssertionError, match="dos veces"):
        configuration_rows(row + row)


def parse_documented_default(cell: str) -> str | float:
    value = cell.strip()
    if value in ("(vacío)", "(vacio)"):
        return ""
    quoted = re.fullmatch(r"`([^`]*)`", value)
    if quoted:
        value = quoted.group(1)
    if value in ("true", "false"):
        return value
    try:
        return float(value)
    except ValueError:
        return value


def normalize_code_default(value: object) -> str | float:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)


def default_mismatches(rows: dict[str, list[str]]) -> list[str]:
    from app.config import Settings

    by_alias = {field.alias: field for field in Settings.model_fields.values() if field.alias}
    mismatches = []
    for name, cells in rows.items():
        field = by_alias.get(name)
        if field is None:  # variables de Compose
            continue
        if parse_documented_default(cells[2]) != normalize_code_default(field.default):
            mismatches.append(f"{name}: documentado {cells[2]!r}, en el codigo {field.default!r}")
    return mismatches


def test_documented_defaults_match_the_settings_defaults():
    assert default_mismatches(configuration_rows()) == []


def test_the_default_checker_catches_a_wrong_default_and_accepts_equivalent_spellings():
    wrong = {"LEVERAGE": ["`LEVERAGE`", "x", "`25`", "y"]}
    right = {
        "LEVERAGE": ["`LEVERAGE`", "x", "`10`", "y"],
        "MAX_DRAWDOWN_PCT": ["`MAX_DRAWDOWN_PCT`", "x", "`0.2`", "y"],
        "ANTHROPIC_API_KEY": ["`ANTHROPIC_API_KEY`", "x", "(vacío)", "y"],
        "PAPER_EXECUTION_ENABLED": ["`PAPER_EXECUTION_ENABLED`", "x", "`false`", "y"],
    }

    assert len(default_mismatches(wrong)) == 1
    assert default_mismatches(right) == []


def test_the_configuration_guide_has_four_columns_per_variable():
    for name, cells in configuration_rows().items():
        assert len(cells) == 4, f"{name}: {len(cells)} columnas"


# --- scripts y opciones --------------------------------------------------------------------------

SCRIPT_REFERENCE = re.compile(r"scripts[\\/]+([a-z_]+\.(?:py|ps1))")
RUN_FORWARD = re.compile(r"\brun ([a-z_]+\.py)\b")
LONG_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
POWERSHELL_FLAG = re.compile(r"(?<![\w-])-([A-Z][A-Za-z]+)\b")
# Opciones de powershell.exe y de Windows que aparecen en las mismas lineas que los scripts.
FOREIGN_POWERSHELL_FLAGS = {
    "NoProfile", "ExecutionPolicy", "File", "Bypass", "WindowStyle", "Hidden",
}


def script_source(name: str) -> str:
    return (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", PUBLIC_DOCS)
def test_cited_scripts_exist(relative):
    missing = sorted({
        name for name in SCRIPT_REFERENCE.findall(read(relative))
        if not (REPO_ROOT / "scripts" / name).is_file()
    })

    assert not missing, f"{relative} cita scripts que no existen: {missing}"


def option_problems(text: str) -> list[str]:
    """En cada linea que cita un script, las opciones que siguen a la cita existen en ese script
    (o en otro citado en la misma linea: `docker_db.py run telegram_daily.py --dry-run`)."""
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        references = list(SCRIPT_REFERENCE.finditer(line))
        if not references:
            continue
        # `docker_db.py run <script>.py --opcion` reenvia las opciones al segundo script.
        names = [m.group(1) for m in references] + RUN_FORWARD.findall(line)
        sources = {
            name: script_source(name) for name in names
            if (REPO_ROOT / "scripts" / name).is_file()
        }
        tail = line[references[0].end():]
        for flag in LONG_FLAG.findall(tail):
            if not any(flag in source for source in sources.values()):
                problems.append(f"linea {number}: {flag} no existe en {sorted(sources)}")
        if any(name.endswith(".ps1") for name in sources):
            for flag in POWERSHELL_FLAG.findall(tail):
                if flag in FOREIGN_POWERSHELL_FLAGS:
                    continue
                pattern = re.compile(rf"\${flag}\b", re.IGNORECASE)
                if not any(pattern.search(source) for source in sources.values()):
                    problems.append(f"linea {number}: -{flag} no existe en {sorted(sources)}")
    return problems


@pytest.mark.parametrize("relative", PUBLIC_DOCS)
def test_cited_script_options_exist(relative):
    problems = option_problems(read(relative))

    assert not problems, f"{relative}: {problems}"


def test_the_option_checker_catches_a_wrong_flag_and_accepts_forwarded_ones():
    good = (
        "python scripts/backup_db.py --keep 14\n"
        "python scripts/docker_db.py run telegram_daily.py --dry-run\n"
        "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\migrate_to_docker.ps1 "
        "-DryRun\n"
    )
    bad = (
        "python scripts/backup_db.py --opcion-inventada\n"
        ".\\scripts\\rollback_from_docker.ps1 -OpcionInventada\n"
    )

    assert option_problems(good) == []
    assert len(option_problems(bad)) == 2


def test_the_docker_migration_guide_only_cites_existing_switches():
    sources = script_source("migrate_to_docker.ps1") + script_source("rollback_from_docker.ps1")
    cited = set(re.findall(r"`-([A-Z][A-Za-z]+)`", read("docs/MUDANZA_DOCKER.md")))

    assert cited, "la guia ya no cita ninguna opcion de los scripts de la mudanza"
    for flag in cited:
        assert re.search(rf"\${flag}\b", sources, re.IGNORECASE), f"-{flag} no existe"


def test_every_script_is_described_in_the_usage_guide():
    usage = read("docs/USO.md")
    undocumented = sorted(
        script.name for script in (REPO_ROOT / "scripts").iterdir()
        if script.is_file() and script.suffix in (".py", ".ps1")
        and script.name not in usage and script.stem not in usage
    )

    assert not undocumented, f"scripts sin describir en docs/USO.md: {undocumented}"


# --- lo que los documentos dicen del producto ----------------------------------------------------


def test_documented_telegram_commands_are_the_real_ones():
    from app.telegram.commands import COMMANDS

    section = read("docs/USO.md").split("\n## Telegram\n", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\|\s*`(/[a-z]+)`\s*\|", section, re.MULTILINE))

    assert documented == set(COMMANDS)


def test_documented_dashboard_tabs_are_the_real_ones():
    tsx = (REPO_ROOT / "frontend" / "src" / "DashboardView.tsx").read_text(encoding="utf-8")
    real = re.findall(r'\{ id: "[a-z]+", label: "([^"]+)" \}', tsx)
    section = read("docs/USO.md").split("\n## El dashboard\n", 1)[1].split("\n## ", 1)[0]
    documented = re.findall(r"^\|\s*\*\*([^*|]+)\*\*\s*\|", section, re.MULTILINE)

    assert real and documented == real


def real_api_routes() -> set[tuple[str, str]]:
    """(metodo, ruta) de todas las operaciones de la API, como las publica su esquema OpenAPI."""
    from app.main import app

    routes = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            routes.add((method.upper(), re.sub(r"\{[^}]*\}", "{}", path)))
    return routes


DOCUMENTED_ROUTE = re.compile(r"`(GET|POST)\s+(/[A-Za-z0-9_/{}|.\-]*)`")


def missing_routes(text: str, real: set[tuple[str, str]]) -> list[str]:
    missing = []
    for method, path in DOCUMENTED_ROUTE.findall(text):
        normalized = re.sub(r"\{[^}]*\}", "{}", path.rstrip("."))
        if (method, normalized) not in real:
            missing.append(f"{method} {path}")
    return missing


def test_documented_api_routes_exist():
    real = real_api_routes()
    missing = [
        f"{relative}: {route}" for relative in PUBLIC_DOCS
        for route in missing_routes(read(relative), real)
    ]

    assert not missing, f"rutas de la API documentadas que no existen: {missing}"


def test_the_route_checker_catches_an_invented_route_and_accepts_placeholders():
    real = real_api_routes()

    assert missing_routes("`GET /api/risk` `POST /api/control/{pause|resume|kill}`", real) == []
    assert missing_routes("`GET /api/inventada` `POST /health`", real) == [
        "GET /api/inventada", "POST /health",
    ]


def test_the_api_has_the_number_of_routes_the_documents_state():
    real = real_api_routes()
    writes = {(method, path) for method, path in real if method != "GET"}

    assert len(real) == 22, "ARQUITECTURA.md e INFORME_FINAL.md dicen 22 rutas: actualiza los docs"
    # login, logout y las acciones de control (una sola ruta con tres acciones)
    assert writes == {
        ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("POST", "/api/control/{}"),
    }


def test_compose_names_match_the_documentation():
    import yaml

    compose = yaml.safe_load(read("docker-compose.yml"))

    assert compose["name"] == "minerva"
    assert compose["services"]["minerva"]["image"] == "minerva:local"
    assert "minerva:local" in read("docs/GUIA_INSTALACION.md")
    assert "minerva:local" in read("README.md")
    assert set(compose["volumes"]) == {"data", "logs"}
    for name in ("minerva_data", "minerva_logs"):
        assert name in read("docs/USO.md")


def flatten(text: str) -> str:
    """El texto sin marcas de cita (>) ni saltos de linea: para buscar frases partidas."""
    return re.sub(r"\s+", " ", text.replace(">", " "))


def test_the_rule_paper_trading_only_is_stated_up_front():
    for relative in (
        "README.md", "docs/GUIA_INSTALACION.md", "docs/COSTOS_Y_LIMITES.md", "docs/README.md",
    ):
        head = flatten(read(relative))[:2500].lower()
        assert "paper trading" in head, relative
    readme = flatten(read("README.md"))[:1800]
    assert "puede perder dinero" in readme
    assert "nada de lo que hay aquí garantiza ganancias" in readme
    assert "no es asesoría financiera" in readme.lower()
    assert "nunca envía órdenes reales" in readme


def test_the_readme_ends_with_an_english_summary():
    english = read("README.md").split("## English summary", 1)[1]

    for heading in ("### About", "### Quick start", "### Disclaimer"):
        assert heading in english
    assert "Not financial advice" in english


def test_unverified_items_are_marked_and_the_bitunix_points_are_listed():
    limits = read("docs/COSTOS_Y_LIMITES.md")
    unverified = limits.split("\n## Lo no verificado\n", 1)[1]

    for needed in (
        "priceProtectScope", "tradeSide", "minTradeVolume", "Latencia", "funding",
        "Linux y macOS", "Telegram", "dashboard", "modo *hedge*",
    ):
        assert needed in unverified, needed
    assert "no verificado" in read("docs/GUIA_INSTALACION.md").lower()
    assert "no verificado" in read("docs/USO.md").lower()


def test_the_cost_page_states_prices_with_their_date_and_source():
    limits = read("docs/COSTOS_Y_LIMITES.md")

    assert "2026-10-23" in limits
    assert "platform.claude.com/docs/en/about-claude/pricing" in limits
    assert "0,0057" in limits and "0,004236" in limits


def test_the_methodology_explains_every_concept_the_task_requires():
    methodology = read("docs/METODOLOGIA.md").lower()

    for concept in (
        "sombra", "aprobada", "rechazada", "sin_llm", "prompt congelado", "conglomerado",
        "n efectivo", "300", "una sola mirada", "placebo", "veredicto", "δ = 3 %",
        "se ocultan los resultados", "qué conclusiones",
    ):
        assert concept in methodology, concept


# --- datos personales y secretos -----------------------------------------------------------------

PERSONAL_PATTERNS = {
    "ruta personal": re.compile("D:[\\\\/]+" + "Estudio|C:[\\\\/]+" + "Users", re.IGNORECASE),
    "nombre propio": re.compile("Renz" + "o", re.IGNORECASE),
    "correo personal": re.compile(r"[\w.+-]+@" + "gmail" + r"\.com", re.IGNORECASE),
    # Nombre por defecto de un equipo de Windows (DESKTOP-ABC1234): en mayusculas, no el texto
    # "Docker Desktop-..." de un enlace.
    "equipo personal": re.compile(r"\bDESKTOP" + "-[A-Z0-9]{5,}" + r"\b"),
}
# El titular de la licencia es el unico lugar donde se nombra a una persona, a pedido del autor.
PERSONAL_DATA_EXCEPTIONS = {"LICENSE", "tests/unit/test_public_docs.py"}
# Algo con forma de correo: usuario@dominio.tld (TLD alfabetico: `vitest@3.2.4` no lo es).
ANY_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
ALLOWED_EMAIL_DOMAINS = (
    "example.com", "example.org", "users.noreply.github.com", "noreply.github.com",
)


def tracked_text_files() -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"],
        capture_output=True, check=True, timeout=120, creationflags=NO_WINDOW,
    ).stdout.decode("utf-8")
    paths = sorted({p for p in listed.split("\0") if p})
    return [p for p in paths if (REPO_ROOT / p).is_file()]


def personal_data_findings(path: str, text: str) -> list[str]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PERSONAL_PATTERNS.items():
            if pattern.search(line):
                findings.append(f"{rule} @ {path}:{number}")
        for email in ANY_EMAIL.findall(line):
            if not email.lower().endswith(ALLOWED_EMAIL_DOMAINS):
                findings.append(f"correo @ {path}:{number}")
    return findings


def test_no_versioned_file_carries_personal_paths_names_or_emails():
    findings = []
    for path in tracked_text_files():
        if path in PERSONAL_DATA_EXCEPTIONS:
            continue
        data = (REPO_ROOT / path).read_bytes()
        if b"\0" in data[:8000]:
            continue
        findings += personal_data_findings(path, data.decode("utf-8", errors="replace"))

    assert not findings, "datos personales (valor no mostrado): " + "; ".join(findings)


def test_the_personal_data_scanner_catches_each_kind_of_finding():
    samples = {
        "ruta personal": "ruta = " + "D:" + "\\Estu" + "dio\\algo",
        "nombre propio": "decision de " + "Ren" + "zo",
        "correo personal": "alguien" + "@gm" + "ail.com",
        "equipo personal": "DESK" + "TOP-ABC1234",
    }

    for rule, line in samples.items():
        assert PERSONAL_PATTERNS[rule].search(line), rule
    assert personal_data_findings("x.md", "escribe a alguien" + "@" + "dominio.org")
    assert not personal_data_findings("x.md", "vitest" + "@3.2.4 y react@18.3.1")
    assert not personal_data_findings("x.md", "(MUDANZA_DOCKER.md#docker-desktop-iniciando)")
    assert not personal_data_findings("x.md", "noreply@" + "users.noreply.github.com")


# --- indices y archivos estandar -----------------------------------------------------------------


def linked_files(relative: str) -> set[str]:
    base = (REPO_ROOT / relative).parent
    found = set()
    for target in links_in(read(relative)):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = (base / target.partition("#")[0]).resolve()
        if path.is_file():
            found.add(path.relative_to(REPO_ROOT).as_posix())
    return found


def test_the_docs_index_links_every_top_level_document():
    linked = linked_files("docs/README.md")
    missing = [
        f"docs/{name}" for name in TOP_LEVEL_DOCS
        if name != "README.md" and f"docs/{name}" not in linked
    ]

    assert not missing, f"docs/README.md no enlaza: {missing}"
    assert "docs/historial/README.md" in linked


def test_the_history_index_links_every_historical_document():
    linked = linked_files("docs/historial/README.md")
    missing = [path for path in HISTORIAL_DOCS if path not in linked]

    assert not missing, f"docs/historial/README.md no enlaza: {missing}"
    assert "docs/INFORME_FINAL.md" in linked


def test_the_history_folder_documents_where_every_moved_file_went():
    text = read("docs/historial/README.md")

    for old in (
        "PROGRESS.md", "docs/SPEC.md", "docs/FASE0.md", "docs/MANUAL_OPERACION.md",
        "docs/MIGRACION_PC.md", "docs/MUDANZA_DOCKER.md",
    ):
        assert old in text, old


def test_the_license_is_mit_with_the_requested_holder_and_year():
    text = read("LICENSE")

    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Renzo Flores" in text
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_contributing_explains_tests_conventions_and_the_frozen_prompt_rule():
    text = read("CONTRIBUTING.md")

    for needed in (
        "pytest", "ruff check .", "npm --prefix frontend run test",
        "npm --prefix frontend run build", "Conventional Commits", "prompt congelado",
        "app/llm/prompts.py", "paper trading", "SEGURIDAD.md",
    ):
        assert needed in text, needed


def test_the_changelog_summarizes_every_phase():
    text = read("CHANGELOG.md")

    for phase in range(0, 8):
        assert f"## Fase {phase} " in text, phase
    assert "Documentación pública" in text


def test_github_templates_exist_and_are_well_formed():
    templates = sorted((REPO_ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.md"))

    assert len(templates) >= 3
    for template in templates:
        head = template.read_text(encoding="utf-8").split("---")[1]
        assert "name:" in head and "about:" in head, template.name
    pull_request = read(".github/pull_request_template.md")
    assert "pytest" in pull_request and "prompt congelado" in pull_request
