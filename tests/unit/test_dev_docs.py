"""Documentacion tecnica para desarrolladores (`docs/dev/`): completa, al dia y coherente.

Lo generico (enlaces y anclas que resuelven, rutas del repositorio citadas que existen, tablas
bien formadas, opciones de scripts y rutas de la API que existen, ningun dato personal) lo vigila
`test_public_docs.py`, que incluye `docs/dev/` y `docs/dev/modulos/`. Aqui queda lo propio de esta
documentacion:

1. Estan todos los documentos prometidos y el indice los enlaza; `docs/README.md` y
   `CONTRIBUTING.md` enlazan la documentacion tecnica.
2. La referencia pdoc, los hechos derivados de las guias y el grafo de paquetes estan al dia
   (`scripts/build_dev_docs.py --check`, sin regenerar).
3. Cada archivo de `app/`, `scripts/` y `frontend/src` tiene su seccion en la guia de su paquete,
   con las etiquetas que prometio el indice y sin textos pendientes.
4. Las funciones y clases que los documentos citan con parentesis (`nombre()`) existen de verdad.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

import pytest

from scripts import build_dev_docs, dev_docs_facts

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV = REPO_ROOT / "docs" / "dev"
GUIDES = DEV / "modulos"

REQUIRED_DOCS = (
    "README.md", "ZONAS_PROTEGIDAS.md", "FLUJO_EJECUCION.md", "MODELO_DATOS.md",
    "ENTORNO_DESARROLLO.md", "PRUEBAS.md", "RECETAS.md",
)
LABEL_RESPONSIBILITY = "**Responsabilidad.**"
LABEL_FUNCTIONS = "**Funciones y clases clave.**"
LABEL_INVARIANTS = "**Invariantes"  # "Invariantes." o "Invariantes — no romper."
LABEL_PITFALLS = "**Trampas conocidas.**"

SECTION = re.compile(r"^### (`[^`]+`(?:[^`\n]*`[^`]+`)*)\s*$", re.MULTILINE)
CALL = re.compile(r"`([A-Za-z_][\w.]*)\(\)`")
FENCE = re.compile(r"^\s*(```|~~~)")

# Nombres que los documentos citan con parentesis y que NO se definen en el repositorio: la
# biblioteca estandar, las dependencias, SQL y el navegador. Si un documento cita una funcion
# nuestra con un nombre que ya no existe, la prueba falla; esta lista solo admite lo ajeno.
EXTERNAL_CALLS = frozenset({
    "chdir", "create_task", "run", "Popen", "sha256", "wait_for", "sleep", "perf_counter",
    "monotonic", "time", "now", "cwd", "resolve", "glob", "read_text", "write_text", "mkdir",
    "execute", "fetch_one", "fetch_all", "commit", "connect", "backup", "close", "model_dump",
    "model_copy", "get", "post", "json", "fetch", "useQuery", "useQueryClient", "useMutation",
    "getattr", "isinstance", "float", "int", "str", "set", "dict", "len", "min", "max", "sorted",
    "open", "print", "input", "next", "zip", "map", "filter", "any", "all", "sum", "abs", "round",
    "parse", "loads", "dumps", "dump", "load", "ensure_future", "gather", "shield", "Event",
    "Lock", "Semaphore", "Queue", "TaskGroup", "uuid4", "getenv", "environ", "exit", "system",
    "backup_db", "count", "query", "unlink", "rename", "replace", "touch", "stat", "exists",
    "is_file", "is_dir", "iterdir", "rglob", "read_bytes", "write_bytes", "relative_to",
    "get_event_loop", "new_event_loop", "run_until_complete", "add_done_callback", "cancel",
    "done", "result", "exception", "cancelled", "set_exception", "set_result",
    "ALTER", "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "PRAGMA", "RAISE", "breakpoint",
})


# --- lectura de los documentos ------------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def prose(text: str) -> str:
    """El texto sin los bloques de codigo (ahi dentro no se cita nada del repositorio)."""
    kept, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def file_sections(text: str) -> dict[str, str]:
    """`ruta del archivo -> cuerpo de su seccion` (`### \\`ruta\\``) en una guia por paquete.

    Un encabezado puede nombrar varios archivos (`### \\`a.ps1\\` y \\`b.ps1\\``): todos comparten
    el mismo cuerpo.
    """
    found: dict[str, str] = {}
    matches = list(SECTION.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        stop = re.search(r"^## ", text[match.end():end], flags=re.MULTILINE)
        if stop is not None:
            end = match.end() + stop.start()
        for path in re.findall(r"`([^`]+)`", match.group(1)):
            found[path] = text[match.end():end]
    return found


def guide_sections() -> dict[str, tuple[str, str]]:
    """`ruta del archivo -> (guia, cuerpo de su seccion)` de TODAS las guias por paquete."""
    found: dict[str, tuple[str, str]] = {}
    for guide in sorted(GUIDES.glob("*.md")):
        for path, body in file_sections(read(guide)).items():
            found[path] = (guide.name, body)
    return found


# --- 1. los documentos y su indice ---------------------------------------------------------------


def test_every_promised_document_exists_and_the_index_links_it():
    index = read(DEV / "README.md")

    for name in REQUIRED_DOCS:
        assert (DEV / name).is_file(), f"falta docs/dev/{name}"
        if name != "README.md":
            assert f"]({name}" in index, f"docs/dev/README.md no enlaza {name}"
    assert "](api/index.html)" in index
    assert (DEV / "api" / "index.html").is_file()


def test_the_index_links_every_package_guide_and_every_guide_has_an_index_entry():
    index = read(DEV / "README.md")
    linked = set(re.findall(r"\]\(modulos/([a-z_]+)\.md", index))
    existing = {guide.stem for guide in GUIDES.glob("*.md")}

    assert linked == existing, f"guias sin enlazar o enlazadas que no existen: {linked ^ existing}"


def test_the_index_has_the_dependency_graph_between_its_markers():
    index = read(DEV / "README.md")
    graph = re.search(
        r"<!-- grafo-paquetes -->\n(.*?)<!-- /grafo-paquetes -->", index, flags=re.DOTALL,
    )

    assert "\n## Mapa de dependencias entre paquetes\n" in index
    assert graph is not None
    assert "```mermaid\nflowchart" in graph.group(1)
    assert "| Paquete | Archivos |" in graph.group(1)


def test_the_documentation_is_linked_from_the_docs_index_and_from_contributing():
    docs_index = read(REPO_ROOT / "docs" / "README.md")
    contributing = read(REPO_ROOT / "CONTRIBUTING.md")

    assert "](dev/README.md" in docs_index
    assert "](dev/ZONAS_PROTEGIDAS.md" in docs_index
    assert "](docs/dev/README.md" in contributing
    assert "](docs/dev/ZONAS_PROTEGIDAS.md" in contributing


def test_the_usage_guide_points_to_the_developer_documentation():
    usage = read(REPO_ROOT / "docs" / "USO.md")

    assert "dev/README.md" in usage


def test_no_developer_document_leaves_markers_of_unfinished_work():
    pattern = re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b|\{\{|\}\}|\[\[")
    found = []
    for document in [*DEV.glob("*.md"), *GUIDES.glob("*.md")]:
        for number, line in enumerate(read(document).splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{document.relative_to(REPO_ROOT).as_posix()}:{number}")
    for guide in GUIDES.glob("*.md"):
        if build_dev_docs.PENDING_TEXT in read(guide):
            found.append(f"{guide.relative_to(REPO_ROOT).as_posix()}: secciones pendientes")

    assert not found, f"marcas de trabajo sin terminar: {found}"


def test_every_developer_document_has_a_title_and_a_not_verified_note_where_it_needs_one():
    for name in REQUIRED_DOCS:
        text = read(DEV / name)
        assert text.startswith("# "), f"{name} no empieza por un titulo"
    for name in ("FLUJO_EJECUCION.md", "MODELO_DATOS.md", "RECETAS.md", "ENTORNO_DESARROLLO.md"):
        assert "## Lo no verificado" in read(DEV / name), f"{name} no declara lo no verificado"
    assert "## Lo que este documento no puede garantizar" in read(DEV / "ZONAS_PROTEGIDAS.md")


# --- 2. lo generado esta al dia -------------------------------------------------------------------


def test_the_generated_documentation_is_up_to_date():
    problems = build_dev_docs.check_all(REPO_ROOT)

    assert not problems, (
        "la documentacion generada esta desactualizada; corre "
        "`python scripts/build_dev_docs.py` y confirma el resultado:\n- " + "\n- ".join(problems)
    )


# --- 3. cada archivo tiene su seccion -------------------------------------------------------------


@cache
def expected_section_paths() -> list[str]:
    """Todos los archivos que deben tener seccion en una guia."""
    paths = [
        path for path in dev_docs_facts.collect_facts(REPO_ROOT)
        if build_dev_docs.needs_section(path, REPO_ROOT)
    ]
    paths += sorted(
        f"scripts/{script.name}" for script in (REPO_ROOT / "scripts").glob("*.ps1")
    )
    paths += sorted(dev_docs_facts.collect_frontend_facts(REPO_ROOT))
    return sorted(paths)


def test_every_source_file_has_a_section_in_its_package_guide():
    documented = guide_sections()
    expected = expected_section_paths()
    missing = [path for path in expected if path not in documented]
    misplaced = [
        f"{path} esta en {guide}"
        for path, (guide, _body) in documented.items()
        if path in expected and guide != f"{build_dev_docs.guide_name(path)}.md"
        and not path.endswith(".ps1")
    ]

    assert not missing, f"archivos sin seccion en su guia: {missing}"
    assert not misplaced, f"secciones en la guia equivocada: {misplaced}"


def test_no_section_documents_a_file_that_no_longer_exists():
    stale = [
        path for path in guide_sections()
        if not (REPO_ROOT / path).is_file()
    ]

    assert not stale, f"secciones de archivos que ya no existen: {stale}"


def public_definitions(path: str) -> int:
    """Cuantas funciones o clases publicas define un archivo Python (1 si es del frontend, 0 si es
    un script de PowerShell, que no tiene la etiqueta)."""
    if path.endswith(".ps1"):
        return 0
    if not path.endswith(".py"):
        return 1
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return sum(
        1 for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and not node.name.startswith("_")
    )


def test_every_file_section_has_the_labels_the_index_promises():
    problems = []
    for path, (guide, body) in guide_sections().items():
        prefix = f"{guide}: {path}"
        if LABEL_RESPONSIBILITY not in body:
            problems.append(f"{prefix} sin {LABEL_RESPONSIBILITY}")
        if LABEL_INVARIANTS not in body:
            problems.append(f"{prefix} sin {LABEL_INVARIANTS}")
        if LABEL_PITFALLS not in body:
            problems.append(f"{prefix} sin {LABEL_PITFALLS}")
        if LABEL_FUNCTIONS not in body and public_definitions(path):
            problems.append(f"{prefix} sin {LABEL_FUNCTIONS}")

    assert not problems, "secciones incompletas:\n- " + "\n- ".join(problems)


def test_every_file_section_has_substance():
    shallow = []
    for path, (guide, body) in guide_sections().items():
        match = re.search(r"\*\*Responsabilidad\.\*\*(.*?)(?=\n\*\*|\Z)", body, flags=re.DOTALL)
        if match is None or len(match.group(1).strip()) < 15:
            shallow.append(f"{guide}: {path}")

    assert not shallow, f"secciones con una responsabilidad vacia o de una palabra: {shallow}"


def test_every_section_carries_its_generated_facts_block():
    for path, (guide, body) in guide_sections().items():
        if path.endswith(".ps1"):
            continue
        assert f"<!-- hechos:{path} -->" in body, f"{guide}: {path} sin su bloque de hechos"
        assert f"<!-- /hechos:{path} -->" in body, f"{guide}: {path} con el bloque sin cerrar"


# --- 4. lo que los documentos citan existe --------------------------------------------------------


@cache
def defined_names() -> frozenset[str]:
    """Todo nombre definido en `app/`, `scripts/` y `tests/` (Python) y en `frontend/src` (TS)."""
    names: set[str] = set()
    for root in ("app", "scripts", "tests"):
        for file in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                    names.add(node.attr)
    declaration = re.compile(r"\b(?:function|const|let|class|interface|type|enum)\s+(\w+)")
    for file in (REPO_ROOT / "frontend" / "src").rglob("*.ts*"):
        names.update(declaration.findall(file.read_text(encoding="utf-8")))
    return frozenset(names)


def cited_calls(text: str) -> set[str]:
    """Los nombres que un documento cita con parentesis vacios (`nombre()` o `Clase.metodo()`)."""
    return set(CALL.findall(prose(text)))


def unknown_calls(text: str) -> list[str]:
    known = defined_names() | EXTERNAL_CALLS
    return sorted(
        token for token in cited_calls(text)
        if token.rpartition(".")[2] not in known
    )


DEV_DOCUMENTS = [
    *sorted(path.relative_to(REPO_ROOT).as_posix() for path in DEV.glob("*.md")),
    *sorted(path.relative_to(REPO_ROOT).as_posix() for path in GUIDES.glob("*.md")),
]


@pytest.mark.parametrize("relative", DEV_DOCUMENTS)
def test_the_functions_and_classes_a_document_cites_exist(relative):
    unknown = unknown_calls(read(REPO_ROOT / relative))

    assert not unknown, (
        f"{relative} cita con parentesis nombres que no existen en el codigo: {unknown}. "
        "Si es una funcion ajena (biblioteca estandar, dependencia), agregala a EXTERNAL_CALLS."
    )


def test_the_citation_checker_catches_an_invented_name_and_ignores_code_blocks():
    text = (
        "Llama a `check_new_entry()` y a `RiskEngine.nombre_inventado()`.\n"
        "```\n`otra_inventada()`\n```\n"
    )

    assert unknown_calls(text) == ["RiskEngine.nombre_inventado"]
    known = "Usa `asyncio.create_task()` y `Settings.max_margin_for_new_trade()`."
    assert unknown_calls(known) == []


def test_the_section_checker_splits_a_guide_by_its_file_headings():
    text = (
        "# Guia\n\n## Archivos\n\n### `app/a.py`\n\ncuerpo a\n\n### `app/b.py`\n\ncuerpo b\n\n"
        "## Otra cosa\n\nno es de b\n"
    )

    sections = file_sections(text)

    assert set(sections) == {"app/a.py", "app/b.py"}
    assert "cuerpo b" in sections["app/b.py"] and "no es de b" not in sections["app/b.py"]
