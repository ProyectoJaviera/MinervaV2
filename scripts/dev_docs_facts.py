"""Hechos derivados del codigo para las guias de `docs/dev/` (imports, tablas, configuracion).

Las guias por modulo (`docs/dev/modulos/*.md`) mezclan narrativa escrita a mano con hechos que se
pueden calcular leyendo el codigo. Este modulo los calcula con `ast` (NUNCA importa `app` ni lee
`.env`) y `scripts/build_dev_docs.py` los escribe entre las marcas `<!-- hechos:RUTA -->` y
`<!-- /hechos:RUTA -->` de cada guia; una prueba falla si alguno quedo viejo.

Alcance de cada hecho (se repite en cada guia para que nadie lo lea de mas):

- **Importa / Lo importan:** imports internos (`app.*`, `scripts.*`), incluidos los perezosos
  dentro de funciones. Es el grafo de IMPORTS, no el de llamadas.
- **Configuracion:** atributos de `Settings` leidos como `settings.X` o `self._settings.X`. No ve
  los que llegan por `getattr` ni por otro nombre de variable.
- **Tablas:** las del SQL escrito en el archivo y las de las funciones de los repositorios
  (`app/persistence/repositories/`) que el archivo llama. No sigue llamadas a otros modulos.
- **Pruebas:** archivos de `tests/` que importan el modulo o citan su ruta; una prueba que lo
  ejercita sin importarlo (p. ej. por la API HTTP) no aparece.

Las rutas se escriben siempre con `/`, relativas a la raiz del repositorio.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOTS = ("app", "scripts")
TESTS_ROOT = "tests"
REPOSITORIES_DIR = "app/persistence/repositories/"
SCHEMA_FILE = "app/persistence/database.py"
SETTINGS_FILE = "app/config.py"
SETTINGS_RECEIVERS = frozenset({"settings", "_settings", "cfg"})
MAX_LISTED = 12
FACTS_HEADER = (
    "**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):"
)

# El marcador `§` ocupa el lugar de las partes dinamicas de un f-string (`FROM {table}`).
DYNAMIC = "§"
_WRITE_SQL = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_]\w*|§)",
    re.IGNORECASE,
)
_DDL_SQL = re.compile(
    r"\b(?:CREATE\s+(?:TEMP\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE"
    r"|DROP\s+TABLE(?:\s+IF\s+EXISTS)?"
    r"|CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\w+\s+ON"
    r"|CREATE\s+TRIGGER(?:\s+IF\s+NOT\s+EXISTS)?\s+\w+\s+(?:BEFORE|AFTER)\s+\w+(?:\s+OF\s+\w+)?\s+ON"
    r")\s+([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_READ_SQL = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*|§)", re.IGNORECASE)
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_]\w*)", re.IGNORECASE
)


@dataclass(frozen=True)
class Source:
    """Un archivo Python ya leido y parseado.

    Attributes:
        path: ruta relativa a la raiz del repositorio, con `/`.
        module: nombre con puntos (`app.trading.risk_engine`; el paquete para `__init__.py`).
        is_package: si es un `__init__.py`.
        text: el codigo fuente.
        tree: su arbol sintactico.
    """

    path: str
    module: str
    is_package: bool
    text: str
    tree: ast.Module


@dataclass(frozen=True)
class Facts:
    """Los hechos de un archivo, listos para escribirse en una guia.

    Attributes:
        path: ruta del archivo.
        module: nombre con puntos.
        imports: modulos internos que importa.
        imported_by: modulos internos que lo importan.
        settings: configuracion que lee (variables de entorno; las propiedades en minusculas).
        reads: tablas que lee.
        writes: tablas que escribe.
        ddl: tablas cuya estructura crea o altera.
        tests: archivos de `tests/` que lo importan o citan su ruta.
        reference: ruta de su pagina en la referencia pdoc (relativa a `docs/dev/api/`), o `None`
            si es un modulo privado.
        lines: cuantas lineas tiene el archivo.
    """

    path: str
    module: str
    imports: tuple[str, ...]
    imported_by: tuple[str, ...]
    settings: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    ddl: tuple[str, ...]
    tests: tuple[str, ...]
    reference: str | None
    lines: int


def module_name(path: str) -> str:
    """El nombre con puntos de un archivo `.py` (el paquete para un `__init__.py`).

    Args:
        path: ruta relativa con `/`.

    Returns:
        El nombre, p. ej. `app.trading.risk_engine`.
    """
    parts = path[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def reference_page(path: str) -> str | None:
    """La ruta de la pagina pdoc de un archivo, o `None` si pdoc no lo documenta.

    pdoc omite los modulos privados (algun componente del nombre empieza con `_`).

    Args:
        path: ruta relativa del archivo con `/`.

    Returns:
        La ruta relativa a `docs/dev/api/` (`app/trading/risk_engine.html`), o `None`.
    """
    parts = module_name(path).split(".")
    if any(part.startswith("_") for part in parts):
        return None
    return "/".join(parts) + ".html"


def load_sources(repo: Path, roots: tuple[str, ...] = SOURCE_ROOTS) -> dict[str, Source]:
    """Lee y parsea todos los `.py` bajo `roots`.

    Args:
        repo: raiz del repositorio.
        roots: carpetas a recorrer.

    Returns:
        Los archivos por ruta, ordenados por ruta.

    Efectos:
        Lee los archivos `.py` del repositorio; no ejecuta nada.
    """
    found: dict[str, Source] = {}
    for root in roots:
        for file in sorted((repo / root).rglob("*.py")):
            if "__pycache__" in file.parts:
                continue
            path = file.relative_to(repo).as_posix()
            text = file.read_text(encoding="utf-8")
            found[path] = Source(
                path, module_name(path), file.name == "__init__.py", text, ast.parse(text)
            )
    return dict(sorted(found.items()))


def _absolute_base(node: ast.ImportFrom, source: Source) -> str | None:
    """El modulo de un `from X import ...` ya absoluto (resuelve los imports relativos)."""
    if node.level == 0:
        return node.module
    package = source.module if source.is_package else source.module.rpartition(".")[0]
    parts = package.split(".")
    if node.level - 1 > len(parts) - 1:
        return None
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def import_bindings(
    source: Source, known: frozenset[str],
) -> tuple[set[str], dict[str, tuple[str, str | None]]]:
    """Los imports internos de un archivo y los nombres locales que dejan.

    Args:
        source: el archivo.
        known: nombres con puntos de los modulos internos.

    Returns:
        La tupla `(modulos importados, nombres locales)`. Cada nombre local apunta a
        `(modulo, atributo)`: el atributo es `None` si el nombre ES el modulo
        (`from app.persistence.repositories import shadow_repo`).
    """
    edges: set[str] = set()
    bindings: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    edges.add(alias.name)
                    if alias.asname:
                        bindings[alias.asname] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_base(node, source)
            if base is None:
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                if f"{base}.{alias.name}" in known:
                    edges.add(f"{base}.{alias.name}")
                    bindings[local] = (f"{base}.{alias.name}", None)
                elif base in known:
                    edges.add(base)
                    bindings[local] = (base, alias.name)
    edges.discard(source.module)
    return edges, bindings


def _terminal(node: ast.expr) -> str:
    """El ultimo nombre de una cadena de atributos (`self._settings` -> `_settings`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def settings_catalog(sources: dict[str, Source]) -> tuple[dict[str, str], set[str]]:
    """Los campos de `Settings` (con su variable de entorno) y sus propiedades.

    Args:
        sources: los archivos del repositorio.

    Returns:
        La tupla `(campo -> variable de entorno, nombres de propiedades y metodos publicos)`.
        La variable de entorno es el `alias` del campo, o su nombre en mayusculas.
    """
    fields: dict[str, str] = {}
    methods: set[str] = set()
    config = sources.get(SETTINGS_FILE)
    if config is None:
        return fields, methods
    for node in config.tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "Settings"):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                alias = None
                if isinstance(item.value, ast.Call):
                    for keyword in item.value.keywords:
                        if keyword.arg == "alias" and isinstance(keyword.value, ast.Constant):
                            alias = str(keyword.value.value)
                fields[item.target.id] = alias or item.target.id.upper()
            elif isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                methods.add(item.name)
    return fields, methods


def settings_used(source: Source, fields: dict[str, str], methods: set[str]) -> set[str]:
    """La configuracion que un archivo lee (`settings.X`, `self._settings.X`, `cfg.X`).

    Args:
        source: el archivo.
        fields: campo de `Settings` -> variable de entorno.
        methods: propiedades y metodos publicos de `Settings`.

    Returns:
        Las variables de entorno de los campos leidos y, en minusculas, los nombres de las
        propiedades. Vacio para `app/config.py`, que los define.
    """
    if source.path == SETTINGS_FILE:
        return set()
    used: set[str] = set()
    for node in ast.walk(source.tree):
        if not isinstance(node, ast.Attribute) or _terminal(node.value) not in SETTINGS_RECEIVERS:
            continue
        if node.attr in fields:
            used.add(fields[node.attr])
        elif node.attr in methods:
            used.add(node.attr)
    return used


def table_catalog(sources: dict[str, Source]) -> frozenset[str]:
    """Los nombres de las tablas que crea el esquema de la base.

    Args:
        sources: los archivos del repositorio.

    Returns:
        Los nombres de las tablas de `Database.SCHEMA` (leidos del SQL, sin importar `app`).
    """
    schema = sources.get(SCHEMA_FILE)
    if schema is None:
        return frozenset()
    return frozenset(name.lower() for name in _CREATE_TABLE.findall(schema.text))


def _docstring_ids(tree: ast.Module) -> set[int]:
    """Los ids de los nodos de los docstrings (no son SQL aunque nombren una tabla)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    ids.add(id(first.value))
    return ids


def _texts(node: ast.AST, skip: set[int]) -> Iterator[str]:
    """Las cadenas bajo `node`; en un f-string, lo dinamico se reemplaza por `§`."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and id(sub) not in skip:
            yield sub.value
        elif isinstance(sub, ast.JoinedStr):
            yield "".join(
                part.value if isinstance(part, ast.Constant) else DYNAMIC for part in sub.values
            )


@dataclass
class Access:
    """Tablas que toca un trozo de codigo.

    Attributes:
        reads: tablas que lee.
        writes: tablas que escribe.
        ddl: tablas cuya estructura crea o altera.
    """

    reads: set[str]
    writes: set[str]
    ddl: set[str]

    def update(self, other: Access) -> None:
        """Suma lo que toca `other`.

        Args:
            other: las tablas a sumar.
        """
        self.reads |= other.reads
        self.writes |= other.writes
        self.ddl |= other.ddl


def sql_access(node: ast.AST, tables: frozenset[str], skip: set[int]) -> Access:
    """Las tablas que toca el SQL escrito bajo `node`.

    Args:
        node: funcion, clase o modulo a revisar.
        tables: nombres de tablas validos (lo que no sea una tabla se ignora).
        skip: ids de los docstrings, que no se miran.

    Returns:
        Las tablas leidas, escritas y alteradas. Con un nombre de tabla dinamico (`FROM {table}`)
        cuenta cada tabla que aparezca como cadena en `node`.
    """
    access = Access(set(), set(), set())
    literals = {text.lower() for text in _texts(node, skip) if text.lower() in tables}
    for text in _texts(node, skip):
        masked = text
        for pattern, target in ((_DDL_SQL, access.ddl), (_WRITE_SQL, access.writes)):
            for match in pattern.finditer(text):
                name = match.group(1).lower()
                if name == DYNAMIC:
                    target |= literals
                elif name in tables:
                    target.add(name)
            masked = pattern.sub(" ", masked)
        for match in _READ_SQL.finditer(masked):
            name = match.group(1).lower()
            if name == DYNAMIC:
                access.reads |= literals
            elif name in tables:
                access.reads.add(name)
    return access


@dataclass
class _Function:
    """Lo que una funcion toca por si misma y a quien llama."""

    access: Access
    local_calls: set[str]
    foreign_calls: set[tuple[str, str]]


def _functions(source: Source) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Las funciones y metodos de un archivo con su nombre calificado (`Clase.metodo`)."""
    for node in source.tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{item.name}", item


def _analyse_functions(
    source: Source, tables: frozenset[str], bindings: dict[str, tuple[str, str | None]],
) -> tuple[dict[str, _Function], Access]:
    """Por funcion: su SQL directo y sus llamadas; mas el SQL de nivel de modulo."""
    skip = _docstring_ids(source.tree)
    names = {qualname for qualname, _ in _functions(source)}
    result: dict[str, _Function] = {}
    inside: set[int] = set()
    for qualname, node in _functions(source):
        owner = qualname.rpartition(".")[0]
        local: set[str] = set()
        foreign: set[tuple[str, str]] = set()
        for sub in ast.walk(node):
            inside.add(id(sub))
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Name):
                if func.id in names:
                    local.add(func.id)
                elif func.id in bindings and bindings[func.id][1] is not None:
                    module, attribute = bindings[func.id]
                    foreign.add((module, str(attribute)))
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "self" and owner and f"{owner}.{func.attr}" in names:
                    local.add(f"{owner}.{func.attr}")
                elif func.value.id in bindings and bindings[func.value.id][1] is None:
                    foreign.add((bindings[func.value.id][0], func.attr))
        result[qualname] = _Function(sql_access(node, tables, skip), local, foreign)
    module_access = Access(set(), set(), set())
    for statement in source.tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(statement, ast.ClassDef):
            for item in statement.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module_access.update(sql_access(item, tables, skip))
            continue
        module_access.update(sql_access(statement, tables, skip))
    return result, module_access


def table_facts(
    sources: dict[str, Source], tables: frozenset[str],
    bindings: dict[str, dict[str, tuple[str, str | None]]],
) -> dict[str, Access]:
    """Las tablas que toca cada archivo: su SQL directo mas las funciones de repositorio que llama.

    Args:
        sources: los archivos del repositorio.
        tables: nombres de tablas validos.
        bindings: nombres locales de cada archivo (`import_bindings`).

    Returns:
        Las tablas leidas, escritas y alteradas por ruta de archivo.
    """
    by_module = {source.module: source for source in sources.values()}
    analysed = {
        path: _analyse_functions(source, tables, bindings[path]) for path, source in sources.items()
    }
    repository_modules = {
        source.module for path, source in sources.items() if path.startswith(REPOSITORIES_DIR)
    }

    def closure(module: str, function: str, seen: set[tuple[str, str]]) -> Access:
        """Lo que toca una funcion de repositorio, con las que ella llama dentro de ellos."""
        total = Access(set(), set(), set())
        if (module, function) in seen or module not in by_module:
            return total
        seen.add((module, function))
        functions = analysed[by_module[module].path][0]
        if function not in functions:
            return total
        info = functions[function]
        total.update(info.access)
        for local in info.local_calls:
            total.update(closure(module, local, seen))
        for other, name in info.foreign_calls:
            if other in repository_modules:
                total.update(closure(other, name, seen))
        return total

    facts: dict[str, Access] = {}
    for path in sources:
        functions, module_access = analysed[path]
        access = Access(set(module_access.reads), set(module_access.writes), set(module_access.ddl))
        for info in functions.values():
            access.update(info.access)
            for module, name in info.foreign_calls:
                if module in repository_modules and module != sources[path].module:
                    access.update(closure(module, name, set()))
        facts[path] = access
    return facts


def tests_by_module(
    repo: Path, sources: dict[str, Source], known: frozenset[str],
) -> dict[str, set[str]]:
    """Que archivos de `tests/` importan cada modulo o citan su ruta.

    Args:
        repo: raiz del repositorio.
        sources: los archivos de `app/` y `scripts/`.
        known: nombres con puntos de los modulos internos.

    Returns:
        Las rutas de prueba por ruta de modulo. Un script cuenta tambien si la prueba nombra su
        archivo (`telegram_alert.py`) junto a la carpeta `scripts` (lo lanza como proceso).

    Efectos:
        Lee los archivos `.py` de `tests/`.
    """
    module_to_path = {source.module: path for path, source in sources.items()}
    found: dict[str, set[str]] = defaultdict(set)
    for file in sorted((repo / TESTS_ROOT).rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(repo).as_posix()
        text = file.read_text(encoding="utf-8")
        tree = ast.parse(text)
        probe = Source(rel, module_name(rel), False, text, tree)
        edges, _ = import_bindings(probe, known)
        for module in edges:
            found[module_to_path[module]].add(rel)
        constants = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        joined = "\n".join(sorted(constants))
        mentions_scripts = "scripts" in joined
        for path in sources:
            name = path.rsplit("/", 1)[-1]
            cited = path in joined or path.replace("/", "\\") in joined
            launched = path.startswith("scripts/") and name in constants and mentions_scripts
            if cited or launched:
                found[path].add(rel)
    return dict(found)


def collect_facts(repo: Path) -> dict[str, Facts]:
    """Calcula los hechos de todos los archivos de `app/` y `scripts/`.

    Args:
        repo: raiz del repositorio.

    Returns:
        Los hechos por ruta de archivo.

    Efectos:
        Lee los `.py` de `app/`, `scripts/` y `tests/`; no ejecuta ni importa nada.
    """
    sources = load_sources(repo)
    known = frozenset(source.module for source in sources.values())
    edges: dict[str, set[str]] = {}
    bindings: dict[str, dict[str, tuple[str, str | None]]] = {}
    for path, source in sources.items():
        edges[path], bindings[path] = import_bindings(source, known)
    importers: dict[str, set[str]] = defaultdict(set)
    for path, targets in edges.items():
        for target in targets:
            importers[target].add(sources[path].module)
    fields, methods = settings_catalog(sources)
    accesses = table_facts(sources, table_catalog(sources), bindings)
    covering = tests_by_module(repo, sources, known)
    facts: dict[str, Facts] = {}
    for path, source in sources.items():
        facts[path] = Facts(
            path=path,
            module=source.module,
            imports=tuple(sorted(edges[path])),
            imported_by=tuple(sorted(importers.get(source.module, ()))),
            settings=tuple(sorted(settings_used(source, fields, methods))),
            reads=tuple(sorted(accesses[path].reads)),
            writes=tuple(sorted(accesses[path].writes)),
            ddl=tuple(sorted(accesses[path].ddl)),
            tests=tuple(sorted(covering.get(path, ()))),
            reference=reference_page(path),
            lines=len(source.text.splitlines()),
        )
    return facts


def package_of(path: str) -> str:
    """El paquete al que pertenece un archivo, para el grafo y para elegir su guia.

    Args:
        path: ruta relativa del archivo con `/`.

    Returns:
        `trading` para `app/trading/x.py`; `config` o `main` para esos archivos de `app/`
        (`app/__init__.py` cuenta como `main`); `scripts` para `scripts/`.
    """
    parts = path.split("/")
    if parts[0] == "scripts":
        return "scripts"
    if len(parts) == 2:
        return "config" if parts[1] == "config.py" else "main"
    return parts[1]


def package_edges(facts: dict[str, Facts]) -> dict[tuple[str, str], int]:
    """Cuantos imports hay de un paquete a otro.

    Args:
        facts: los hechos de `collect_facts`.

    Returns:
        `(paquete, paquete importado) -> cantidad de pares (archivo, modulo importado)`, sin los
        imports dentro de un mismo paquete.
    """
    module_to_path = {item.module: path for path, item in facts.items()}
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for path, item in facts.items():
        source_package = package_of(path)
        for target in item.imports:
            target_package = package_of(module_to_path[target])
            if target_package != source_package:
                counts[(source_package, target_package)] += 1
    return dict(sorted(counts.items()))


def _group_of(item: str) -> str:
    """El paquete de un modulo o la carpeta de una prueba, para resumir listas largas."""
    parts = item.split(".") if "." in item and "/" not in item else item.split("/")
    if parts[0] == "app" and len(parts) > 1:
        return parts[1]
    if parts[0] == "tests":
        return parts[1] if len(parts) > 2 else "tests/"
    return parts[0]


def _names(items: tuple[str, ...], noun: str = "") -> str:
    """Una lista como texto: completa, o resumida por paquete si pasa de `MAX_LISTED`.

    Con `noun` vacio (configuracion y tablas) la lista nunca se resume.
    """
    if not items:
        return "ninguno"
    if not noun or len(items) <= MAX_LISTED:
        return ", ".join(f"`{item}`" for item in items)
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[_group_of(item)] += 1
    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    return f"{len(items)} {noun} ({summary})"


def render_facts(item: Facts, guide_dir_to_api: str = "../api") -> str:
    """El bloque de hechos de un archivo, en Markdown.

    Args:
        item: los hechos del archivo.
        guide_dir_to_api: ruta relativa de la guia a `docs/dev/api/`.

    Returns:
        Las lineas del bloque, sin las marcas.
    """
    page = (
        f"[`api/{item.reference}`]({guide_dir_to_api}/{item.reference})"
        if item.reference else "sin página (módulo privado)"
    )
    return "\n".join([
        FACTS_HEADER,
        "",
        f"- **Referencia pdoc:** {page}",
        f"- **Importa (internos):** {_names(item.imports, 'módulos')}",
        f"- **Lo importan:** {_names(item.imported_by, 'módulos')}",
        f"- **Configuración que lee:** {_names(item.settings)}",
        f"- **Tablas que lee:** {_names(item.reads)}",
        f"- **Tablas que escribe:** {_names(item.writes)}",
        f"- **Tablas que crea o altera:** {_names(item.ddl)}",
        f"- **Pruebas que lo importan o citan:** {_names(item.tests, 'archivos')}",
    ])


# --- Grafo de paquetes -------------------------------------------------------------------------

DIAGRAM_MIN_IMPORTS = 3
DIAGRAM_HIDDEN_TARGETS = ("config", "persistence")
DIAGRAM_HIDDEN_SOURCES = ("main", "scripts")


def package_sizes(facts: dict[str, Facts]) -> dict[str, tuple[int, int]]:
    """Cuantos archivos y lineas tiene cada paquete.

    Args:
        facts: los hechos de `collect_facts`.

    Returns:
        `paquete -> (archivos, lineas)`, ordenado por nombre.
    """
    sizes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for path, item in facts.items():
        sizes[package_of(path)][0] += 1
        sizes[package_of(path)][1] += item.lines
    return {name: (files, lines) for name, (files, lines) in sorted(sizes.items())}


def render_mermaid(facts: dict[str, Facts]) -> str:
    """El diagrama Mermaid de dependencias entre paquetes (el filtrado que se explica al pie).

    Dibuja solo las flechas con `DIAGRAM_MIN_IMPORTS` imports o mas; omite las que llegan a los
    paquetes de base (`DIAGRAM_HIDDEN_TARGETS`) y las que salen de los que lo ensamblan todo
    (`DIAGRAM_HIDDEN_SOURCES`). La tabla completa va aparte (`render_dependency_table`).

    Args:
        facts: los hechos de `collect_facts`.

    Returns:
        El bloque de codigo Mermaid, con sus cercas.
    """
    sizes = package_sizes(facts)
    drawn = {
        edge: count for edge, count in package_edges(facts).items()
        if count >= DIAGRAM_MIN_IMPORTS
        and edge[0] not in DIAGRAM_HIDDEN_SOURCES and edge[1] not in DIAGRAM_HIDDEN_TARGETS
    }
    nodes = sorted({name for edge in drawn for name in edge})
    lines = ["```mermaid", "flowchart LR"]
    for name in nodes:
        lines.append(f'    {name}["{name}<br/>{sizes[name][0]} archivos"]')
    for (source, target), count in drawn.items():
        lines.append(f"    {source} -->|{count}| {target}")
    lines.append("```")
    return "\n".join(lines)


def render_dependency_table(facts: dict[str, Facts]) -> str:
    """La tabla completa de dependencias entre paquetes, con tamanos.

    Args:
        facts: los hechos de `collect_facts`.

    Returns:
        Una tabla Markdown: paquete, archivos, lineas, de que paquetes importa (con el numero de
        imports) y quien lo importa.
    """
    sizes = package_sizes(facts)
    edges = package_edges(facts)
    rows = [
        "| Paquete | Archivos | Líneas | Importa de (imports) | Lo importan |",
        "|---|---:|---:|---|---|",
    ]
    for name, (files, lines) in sizes.items():
        out = sorted(
            ((b, n) for (a, b), n in edges.items() if a == name), key=lambda x: (-x[1], x[0])
        )
        into = sorted(a for (a, b) in edges if b == name)
        rows.append(
            f"| `{name}` | {files} | {lines} | "
            + (", ".join(f"{b} {n}" for b, n in out) or "ninguno") + " | "
            + (", ".join(into) or "ninguno") + " |"
        )
    return "\n".join(rows)


def render_graph(facts: dict[str, Facts]) -> str:
    """El bloque completo del grafo de paquetes para `docs/dev/README.md`.

    Args:
        facts: los hechos de `collect_facts`.

    Returns:
        El diagrama, su nota de filtrado y la tabla completa.
    """
    hidden_targets = ", ".join(f"`{name}`" for name in DIAGRAM_HIDDEN_TARGETS)
    hidden_sources = ", ".join(f"`{name}`" for name in DIAGRAM_HIDDEN_SOURCES)
    return "\n".join([
        render_mermaid(facts),
        "",
        f"El diagrama muestra solo las flechas con {DIAGRAM_MIN_IMPORTS} o más imports; no dibuja "
        f"las que llegan a {hidden_targets} (casi todos los paquetes las usan) ni las que salen de "
        f"{hidden_sources} (ensamblan todo). El número de cada flecha es la cantidad de imports. "
        "La tabla de abajo tiene todas las dependencias.",
        "",
        render_dependency_table(facts),
    ])


# --- Frontend ----------------------------------------------------------------------------------

FRONTEND_SRC = "frontend/src"
_TS_IMPORT = re.compile(
    r"""(?:import|export)\s[^;]*?from\s+['"](\.{1,2}/[^'"]+)['"]"""
    r"""|import\s+['"](\.{1,2}/[^'"]+)['"]"""
)
_ENDPOINT = re.compile(r"""["'`](/(?:api/[A-Za-z0-9_/\-${}.]*|health))["'`?]""")


@dataclass(frozen=True)
class FrontendFacts:
    """Los hechos de un archivo de `frontend/src/`.

    Attributes:
        path: ruta relativa a la raiz del repositorio.
        imports: archivos de `frontend/src/` que importa (imports relativos).
        imported_by: archivos que lo importan.
        endpoints: rutas HTTP del backend que cita como cadena.
        tests: archivos de prueba del mismo componente (`X.test.tsx` para `X.tsx`) y los que lo
            importan.
        lines: cuantas lineas tiene.
    """

    path: str
    imports: tuple[str, ...]
    imported_by: tuple[str, ...]
    endpoints: tuple[str, ...]
    tests: tuple[str, ...]
    lines: int


def _resolve_relative(origin: str, target: str, files: set[str]) -> str | None:
    """El archivo de `frontend/src/` al que apunta un import relativo, si existe."""
    base = Path(origin).parent.joinpath(target)
    parts: list[str] = []
    for part in base.as_posix().split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    stem = "/".join(parts)
    for candidate in (stem, f"{stem}.ts", f"{stem}.tsx", f"{stem}/index.ts", f"{stem}/index.tsx"):
        if candidate in files:
            return candidate
    return None


def collect_frontend_facts(repo: Path) -> dict[str, FrontendFacts]:
    """Calcula los hechos de los archivos TypeScript de `frontend/src/`.

    Args:
        repo: raiz del repositorio.

    Returns:
        Los hechos por ruta de archivo (sin `*.test.*`, que se cuentan como pruebas).

    Efectos:
        Lee los `.ts` y `.tsx` de `frontend/src/`.
    """
    root = repo / FRONTEND_SRC
    texts = {
        file.relative_to(repo).as_posix(): file.read_text(encoding="utf-8")
        for file in sorted(root.rglob("*")) if file.suffix in (".ts", ".tsx")
    }
    names = set(texts)
    imports: dict[str, set[str]] = {}
    for path, text in texts.items():
        found: set[str] = set()
        for match in _TS_IMPORT.finditer(text):
            target = _resolve_relative(path, match.group(1) or match.group(2), names)
            if target and target != path:
                found.add(target)
        imports[path] = found
    importers: dict[str, set[str]] = defaultdict(set)
    for path, targets in imports.items():
        for target in targets:
            importers[target].add(path)
    facts: dict[str, FrontendFacts] = {}
    for path, text in texts.items():
        if ".test." in path:
            continue
        stem = path.rsplit(".", 1)[0]
        tests = {other for other in texts if other.startswith(stem + ".test.")}
        tests |= {other for other in importers.get(path, ()) if ".test." in other}
        facts[path] = FrontendFacts(
            path=path,
            imports=tuple(sorted(t for t in imports[path] if ".test." not in t)),
            imported_by=tuple(sorted(o for o in importers.get(path, ()) if ".test." not in o)),
            endpoints=tuple(sorted(set(_ENDPOINT.findall(text)))),
            tests=tuple(sorted(tests)),
            lines=len(text.splitlines()),
        )
    return facts


def render_frontend_facts(item: FrontendFacts) -> str:
    """El bloque de hechos de un archivo del frontend, en Markdown.

    Args:
        item: los hechos del archivo.

    Returns:
        Las lineas del bloque, sin las marcas.
    """
    return "\n".join([
        FACTS_HEADER,
        "",
        f"- **Importa (de `frontend/src/`):** {_names(item.imports)}",
        f"- **Lo importan:** {_names(item.imported_by)}",
        f"- **Rutas HTTP que cita:** {_names(item.endpoints)}",
        f"- **Pruebas:** {_names(item.tests)}",
    ])
