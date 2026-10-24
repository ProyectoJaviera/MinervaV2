"""Genera la referencia de la API interna (pdoc) y los hechos de las guias de `docs/dev/`.

Hace tres cosas, todas derivadas del codigo:

1. **Referencia HTML** de todos los modulos publicos de `app/` y `scripts/` con pdoc en
   `docs/dev/api/` (abrir `docs/dev/api/index.html` en un navegador; no necesita red). Los
   docstrings siguen el formato de Google (`Args:`, `Returns:`, `Raises:`) mas la seccion propia
   `Efectos:`; `tests/unit/test_docstrings.py` exige que todo lo publico los tenga.
2. **Hechos de las guias** `docs/dev/modulos/*.md`: entre las marcas `<!-- hechos:RUTA -->` y
   `<!-- /hechos:RUTA -->` de cada archivo escribe sus imports, la configuracion que lee, las tablas
   que toca y las pruebas que lo importan (`scripts/dev_docs_facts.py`). La narrativa es a mano.
3. **Grafo de paquetes** (Mermaid y tabla) en `docs/dev/README.md`, entre `<!-- grafo-paquetes -->`
   y `<!-- /grafo-paquetes -->`.

    python scripts/build_dev_docs.py                  # regenera lo que este desactualizado
    python scripts/build_dev_docs.py --force          # regenera tambien la referencia pdoc
    python scripts/build_dev_docs.py --check          # solo verifica (sale con 1 si hay algo viejo)
    python scripts/build_dev_docs.py --check --strict # ademas regenera en una carpeta temporal

**Como sabe que la referencia esta vieja.** `docs/dev/api/manifest.json` guarda, por cada archivo
de `app/` y `scripts/`, la huella de su SUPERFICIE DOCUMENTADA (`documentation_surface`): firmas,
docstrings, constantes y atributos, sin los cuerpos de las funciones. Cambiar la logica de una
funcion no pide regenerar; cambiar una firma, un docstring o una constante, si. El manifiesto
tambien guarda la huella de cada pagina HTML, para detectar paginas editadas a mano o a medias. Con
`--strict` (y solo si pdoc, Python y sistema operativo son los del manifiesto) se regenera todo en
una carpeta temporal y se compara pagina por pagina.

**Seguridad.** pdoc IMPORTA los modulos para leerlos. Para que eso no lea secretos ni deje rastros:
corre en un proceso aparte (`--worker`) con el directorio de trabajo en una carpeta temporal vacia
(asi `Settings()` no encuentra ningun `.env`), con las variables de entorno de `.env.example`
quitadas, con `os.chdir` anulado (varios scripts lo llaman al importarse) y ocultando `settings`.
Despues reemplaza las rutas locales por `<repo>` y se niega a publicar una pagina que aun muestre
una ruta de la carpeta personal. `PYTHONHASHSEED=0` fija el orden de los `frozenset`, que pdoc
imprime tal cual.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import dev_docs_facts  # noqa: E402
from scripts.docker_support import NO_WINDOW  # noqa: E402

FORMAT_VERSION = 1
PDOC_VERSION = "16.0.0"
API_DIR = "docs/dev/api"
MANIFEST_NAME = "manifest.json"
GUIDES_DIR = "docs/dev/modulos"
DEV_README = "docs/dev/README.md"
FOOTER = "Referencia generada por scripts/build_dev_docs.py desde los docstrings; no se edita."
WORKER_TIMEOUT_SECONDS = 900
WORKER_FLAG = "MINERVA_DOCS_WORKER"
PLACEHOLDER = "&lt;repo&gt;"
CWD_PLACEHOLDER = "&lt;cwd&gt;"
# El directorio de trabajo del proceso de pdoc es una carpeta temporal de nombre aleatorio; algun
# script lo guarda en una constante al importarse (`LAUNCH_DIR = Path.cwd()`) y pdoc la imprime.
WORKER_CWD_PATH = re.compile(r"(?:[A-Za-z]:|/)[^'\"&;<>\s]*?minerva_docs_cwd_\w+")
FACTS_MARK = re.compile(
    r"(?P<open><!-- hechos:(?P<path>\S+) -->)\n(?P<body>.*?)(?P<close><!-- /hechos:(?P=path) -->)",
    re.DOTALL,
)
GRAPH_MARK = re.compile(
    r"(?P<open><!-- grafo-paquetes -->)\n(?P<body>.*?)(?P<close><!-- /grafo-paquetes -->)",
    re.DOTALL,
)
SECRET_PATTERNS = (
    ("clave de Anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("hash bcrypt", re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{40,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}")),
    ("token de Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}")),
)
PENDING_TEXT = "_Pendiente de documentar._"
SOURCE_MESSAGES = (
    "archivo nuevo que no esta en el manifiesto",
    "archivo borrado que sigue en el manifiesto",
    "superficie documentada cambiada",
)
PAGE_MESSAGES = (
    "pagina sin huella en el manifiesto",
    "pagina del manifiesto que no existe",
    "pagina distinta a la del manifiesto",
)


# --- Superficie documentada y huellas ---------------------------------------------------------

def _is_string_statement(node: ast.stmt) -> bool:
    """Si la sentencia es solo una cadena (un docstring de modulo, clase, funcion o variable)."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _header(node: ast.AST, lines: list[str]) -> str:
    """Las lineas de la cabecera de una funcion, clase o `if`: decoradores incluidos."""
    decorators = getattr(node, "decorator_list", [])
    first = min([item.lineno for item in decorators] + [node.lineno])  # type: ignore[attr-defined]
    body = node.body  # type: ignore[attr-defined]
    last = max(node.lineno, body[0].lineno - 1) if body else node.lineno  # type: ignore[attr-defined]
    return "\n".join(lines[first - 1:last])


def _is_self_assignment(node: ast.stmt) -> bool:
    """Si es `self.x = ...` o `self.x: T = ...`, que pdoc documenta como atributo de la clase."""
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return any(
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        for target in targets
    )


def _surface(body: list[ast.stmt], source: str, lines: list[str], out: list[str]) -> None:
    """Agrega a `out` el texto de lo que pdoc puede mostrar de las sentencias de `body`."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_header(node, lines))
            statements = node.body
            if statements and _is_string_statement(statements[0]):
                out.append(ast.get_source_segment(source, statements[0]) or "")
                statements = statements[1:]
            if node.name == "__init__":
                for inner in statements:
                    if _is_self_assignment(inner) or _is_string_statement(inner):
                        out.append(ast.get_source_segment(source, inner) or "")
        elif isinstance(node, ast.ClassDef):
            out.append(_header(node, lines))
            _surface(node.body, source, lines, out)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
            out.append(ast.get_source_segment(source, node) or "")
        elif _is_string_statement(node):
            out.append(ast.get_source_segment(source, node) or "")
        elif isinstance(node, ast.If):
            out.append(_header(node, lines))
            _surface(node.body, source, lines, out)
            if node.orelse:
                out.append("else:")
                _surface(node.orelse, source, lines, out)
        elif isinstance(node, ast.Try):
            _surface(node.body, source, lines, out)
            for handler in node.handlers:
                _surface(handler.body, source, lines, out)
            _surface(node.orelse, source, lines, out)
            _surface(node.finalbody, source, lines, out)


def documentation_surface(source: str) -> str:
    """El texto de lo que pdoc puede mostrar de un archivo, sin los cuerpos de las funciones.

    Incluye: el docstring del modulo, las cabeceras (decoradores y firma) y los docstrings de
    funciones y clases, las asignaciones de modulo y de clase (pdoc imprime sus valores), los
    imports (de ellos salen los enlaces entre paginas) y las asignaciones `self.x = ...` de
    `__init__` (pdoc las documenta como atributos). Todo se toma del texto fuente, no de
    `ast.unparse`, para que no dependa de la version de Python.

    Args:
        source: el codigo fuente del archivo.

    Returns:
        El texto canonico de la superficie.

    Raises:
        SyntaxError: si el archivo no se puede parsear.
    """
    source = source.replace("\r\n", "\n")
    tree = ast.parse(source)
    out: list[str] = []
    _surface(tree.body, source, source.split("\n"), out)
    return "\n".join(out)


def surface_digest(source: str) -> str:
    """La huella SHA-256 de la superficie documentada de un archivo.

    Args:
        source: el codigo fuente.

    Returns:
        La huella en hexadecimal.
    """
    return hashlib.sha256(documentation_surface(source).encode("utf-8")).hexdigest()


def source_digests(repo: Path) -> dict[str, str]:
    """La huella de superficie de cada `.py` de `app/` y `scripts/`.

    Args:
        repo: raiz del repositorio.

    Returns:
        `ruta -> huella`, ordenado por ruta.

    Raises:
        SyntaxError: si algun archivo no se puede parsear.

    Efectos:
        Lee los `.py` de `app/` y `scripts/`.
    """
    digests: dict[str, str] = {}
    for root in dev_docs_facts.SOURCE_ROOTS:
        for file in sorted((repo / root).rglob("*.py")):
            if "__pycache__" in file.parts:
                continue
            digests[file.relative_to(repo).as_posix()] = surface_digest(
                file.read_text(encoding="utf-8")
            )
    return dict(sorted(digests.items()))


def page_digests(api_dir: Path) -> dict[str, str]:
    """La huella SHA-256 de cada archivo de la referencia (menos el manifiesto).

    Los saltos de linea se normalizan a LF antes de calcularla, para que un checkout con CRLF no
    cambie nada.

    Args:
        api_dir: carpeta de la referencia.

    Returns:
        `ruta relativa -> huella`, ordenado por ruta.

    Efectos:
        Lee los archivos de `api_dir`.
    """
    digests: dict[str, str] = {}
    if api_dir.is_dir():
        for file in sorted(api_dir.rglob("*")):
            if file.is_file() and file.name != MANIFEST_NAME:
                data = file.read_bytes().replace(b"\r\n", b"\n")
                digests[file.relative_to(api_dir).as_posix()] = hashlib.sha256(data).hexdigest()
    return digests


def installed_pdoc_version() -> str | None:
    """La version de pdoc instalada, o `None` si no esta instalado.

    Returns:
        El numero de version, p. ej. `16.0.0`.
    """
    try:
        return importlib.metadata.version("pdoc")
    except importlib.metadata.PackageNotFoundError:
        return None


def current_environment() -> dict[str, str]:
    """Con que se genera la referencia: pdoc, Python y sistema operativo.

    Returns:
        Un diccionario `pdoc`, `python` (mayor.menor) y `plataforma`.
    """
    return {
        "pdoc": installed_pdoc_version() or "",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "plataforma": sys.platform,
    }


def build_manifest(repo: Path, environment: dict[str, str] | None = None) -> dict:
    """El manifiesto de la referencia: formato, entorno, huellas de fuentes y de paginas.

    Args:
        repo: raiz del repositorio.
        environment: con que se genero; por defecto, el entorno actual.

    Returns:
        El diccionario del manifiesto.

    Efectos:
        Lee los `.py` de `app/` y `scripts/` y los archivos de `docs/dev/api/`.
    """
    return {
        "formato": FORMAT_VERSION,
        "generador": "scripts/build_dev_docs.py",
        "entorno": environment or current_environment(),
        "fuentes": source_digests(repo),
        "paginas": page_digests(repo / API_DIR),
    }


def write_manifest(repo: Path, environment: dict[str, str] | None = None) -> None:
    """Escribe `docs/dev/api/manifest.json` con el estado actual de fuentes y paginas.

    Args:
        repo: raiz del repositorio.
        environment: con que se genero; por defecto, el entorno actual.

    Efectos:
        Escribe `docs/dev/api/manifest.json` (UTF-8, saltos LF).
    """
    manifest = build_manifest(repo, environment)
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target = repo / API_DIR / MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))


def read_manifest(repo: Path) -> dict | None:
    """Lee el manifiesto de la referencia, o `None` si no existe o no es JSON valido.

    Args:
        repo: raiz del repositorio.

    Returns:
        El diccionario del manifiesto, o `None`.

    Efectos:
        Lee `docs/dev/api/manifest.json`.
    """
    path = repo / API_DIR / MANIFEST_NAME
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _changes(
    recorded: dict[str, str], current: dict[str, str], messages: tuple[str, str, str],
) -> list[str]:
    """Una linea por cada archivo agregado, borrado o cambiado entre dos mapas de huellas."""
    added, removed, changed = messages
    problems = []
    for path in sorted(set(recorded) | set(current)):
        if path not in recorded:
            problems.append(f"{added}: {path}")
        elif path not in current:
            problems.append(f"{removed}: {path}")
        elif recorded[path] != current[path]:
            problems.append(f"{changed}: {path}")
    return problems


def api_problems(repo: Path, *, strict: bool = False) -> list[str]:
    """Lo que esta desactualizado en `docs/dev/api/`; vacio si esta al dia.

    La verificacion normal es portable (solo lee archivos): compara la superficie documentada
    actual con la del manifiesto y las paginas con sus huellas. Con `strict` regenera ademas la
    referencia en una carpeta temporal y la compara pagina por pagina, pero solo si pdoc, Python y
    el sistema operativo son los del manifiesto (si no, lo dice y no compara).

    Args:
        repo: raiz del repositorio.
        strict: regenerar y comparar de verdad.

    Returns:
        Una descripcion por problema.

    Efectos:
        Lee fuentes y referencia; con `strict`, corre pdoc en un proceso aparte y escribe en una
        carpeta temporal.
    """
    manifest = read_manifest(repo)
    fix = "corre `python scripts/build_dev_docs.py`"
    if manifest is None:
        return [f"falta {API_DIR}/{MANIFEST_NAME} (o no es JSON valido): {fix}"]
    if manifest.get("formato") != FORMAT_VERSION:
        return [f"el manifiesto es de otro formato ({manifest.get('formato')}): {fix} --force"]
    try:
        current = source_digests(repo)
    except SyntaxError as exc:
        return [f"un archivo no se puede parsear ({exc}); la referencia no se puede verificar"]
    problems = _changes(manifest.get("fuentes", {}), current, SOURCE_MESSAGES)
    problems += _changes(
        manifest.get("paginas", {}), page_digests(repo / API_DIR), PAGE_MESSAGES
    )
    if problems:
        return [*problems, fix]
    if strict:
        return _strict_problems(repo, manifest)
    return []


def _strict_problems(repo: Path, manifest: dict) -> list[str]:
    """Regenera la referencia en una carpeta temporal y la compara con el manifiesto."""
    recorded = manifest.get("entorno", {})
    now = current_environment()
    if recorded != now:
        return [
            f"--strict no aplica: la referencia se genero con {recorded} y este entorno es {now}"
        ]
    with tempfile.TemporaryDirectory(prefix="minerva_docs_strict_") as tmp:
        output = Path(tmp) / "api"
        generate_reference(repo, output)
        normalize_tree(output, repo)
        fresh = page_digests(output)
    differences = _changes(manifest.get("paginas", {}), fresh, PAGE_MESSAGES)
    if differences:
        return [*differences, "corre `python scripts/build_dev_docs.py --force`"]
    return []


# --- Generacion con pdoc -----------------------------------------------------------------------

def scrubbed_environment(repo: Path) -> dict[str, str]:
    """El entorno del proceso que corre pdoc: sin las variables de `.env.example`.

    Lee SOLO los nombres de `.env.example` (nunca `.env`) para quitarlos del entorno heredado, y
    fija `PYTHONHASHSEED=0` (orden estable de los `frozenset` que pdoc imprime), `PYTHONUTF8=1` y
    `PYTHONDONTWRITEBYTECODE=1`.

    Args:
        repo: raiz del repositorio.

    Returns:
        El diccionario de entorno para `subprocess.run`.

    Efectos:
        Lee `.env.example`.
    """
    example = repo / ".env.example"
    names: set[str] = set()
    if example.is_file():
        text = example.read_text(encoding="utf-8")
        names = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))
    env = {
        key: value for key, value in os.environ.items()
        if key not in names and not key.startswith("MINERVA_")
    }
    env.update({
        "PYTHONHASHSEED": "0", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", WORKER_FLAG: "1",
    })
    return env


def _ignore_chdir(*_args: object, **_kwargs: object) -> None:
    """Sustituye a `os.chdir` en el proceso de pdoc: no hace nada."""


def _redacted_repr(_self: object) -> str:
    """Sustituye al `repr` de `Settings` en el proceso de pdoc: nunca muestra valores."""
    return "Settings(...)"


def render_reference(output: Path) -> None:
    """Corre pdoc sobre `app` y `scripts` EN ESTE proceso; solo lo llama `--worker`.

    Importa los modulos, asi que SOLO corre en el proceso limpio que prepara `generate_reference`
    (marca de entorno, directorio de trabajo vacio y distinto del repositorio): si no, se niega
    ANTES de importar nada, porque `Settings()` leeria el `.env` del directorio de trabajo. Ademas
    anula `os.chdir` (varios scripts lo llaman al importarse) y reemplaza el `repr` de `Settings`:
    pdoc imprime el valor de `settings` y, asi, nunca mostraria valores aunque el aislamiento
    fallara.

    Args:
        output: carpeta donde escribir el HTML.

    Raises:
        SystemExit: si no lo lanzo `generate_reference`.
        UserWarning: si pdoc avisa de algo (por ejemplo un docstring que no puede interpretar).

    Efectos:
        Importa todos los modulos de `app/` y `scripts/`; escribe el HTML en `output`.
    """
    if (
        os.environ.get(WORKER_FLAG) != "1"
        or Path(".env").exists()
        or Path.cwd().resolve() == REPO_ROOT
    ):
        raise SystemExit(
            "--worker es de uso interno: lo lanza generate_reference en un directorio vacio"
        )
    os.chdir = _ignore_chdir  # type: ignore[assignment]
    import pdoc
    import pdoc.render

    import app.config

    app.config.Settings.__repr__ = _redacted_repr  # type: ignore[method-assign]
    warnings.filterwarnings("error", module=r"pdoc(\.|$)")
    pdoc.render.configure(
        docformat="google", show_source=False, search=False, footer_text=FOOTER,
    )
    pdoc.pdoc("app", "scripts", output_directory=output)


def generate_reference(repo: Path, output: Path) -> None:
    """Genera el HTML de pdoc en `output` desde un proceso aparte y aislado.

    Args:
        repo: raiz del repositorio.
        output: carpeta de salida.

    Raises:
        RuntimeError: si pdoc no esta instalado, es de otra version o el proceso falla.

    Efectos:
        Lanza `python scripts/build_dev_docs.py --worker` con el directorio de trabajo en una
        carpeta temporal vacia y el entorno de `scrubbed_environment`.
    """
    version = installed_pdoc_version()
    if version != PDOC_VERSION:
        raise RuntimeError(
            f"se necesita pdoc=={PDOC_VERSION} (instalado: {version}): "
            f"`pip install pdoc=={PDOC_VERSION}` o `uv pip install pdoc=={PDOC_VERSION}`"
        )
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="minerva_docs_cwd_") as cwd:
        completed = subprocess.run(
            [sys.executable, "-s", str(script), "--worker", str(output)],
            cwd=cwd, env=scrubbed_environment(repo), capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
            timeout=WORKER_TIMEOUT_SECONDS, check=False,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-3000:]
        raise RuntimeError(f"pdoc fallo (codigo {completed.returncode}):\n{detail}")


def _local_path_variants(path: Path) -> list[str]:
    """Las formas en que una ruta local puede quedar escrita en una pagina."""
    posix, native = path.as_posix(), str(path)
    return sorted({posix, native, native.replace("\\", "\\\\")}, key=len, reverse=True)


def normalize_page(data: bytes, repo: Path) -> bytes:
    """Deja una pagina portable: saltos LF, `<repo>` en vez de la ruta local y `Path(...)`.

    pdoc imprime el valor de las constantes de modulo, y algunas son rutas absolutas
    (`REPO_ROOT / "docs"`): se sustituyen para no publicar la carpeta personal ni depender del
    sistema operativo (`WindowsPath(`/`PosixPath(` pasan a `Path(`).

    Args:
        data: el HTML tal como lo escribio pdoc.
        repo: raiz del repositorio.

    Returns:
        El HTML normalizado, en UTF-8.
    """
    text = data.decode("utf-8").replace("\r\n", "\n")
    text = WORKER_CWD_PATH.sub(CWD_PLACEHOLDER, text)
    for variant in _local_path_variants(repo):
        text = re.sub(re.escape(variant), PLACEHOLDER, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:Windows|Posix)Path\(", "Path(", text)
    return text.encode("utf-8")


def local_path_leaks(text: str, repo: Path) -> list[str]:
    """Las rutas locales que aun aparecen en un texto (carpeta del repositorio o personal).

    Args:
        text: el HTML ya normalizado.
        repo: raiz del repositorio.

    Returns:
        Las rutas encontradas; vacio si no hay fugas.
    """
    leaks = []
    for candidate in (repo, Path.home()):
        if len(str(candidate)) < 7:
            continue
        for variant in _local_path_variants(candidate):
            if re.search(re.escape(variant), text, flags=re.IGNORECASE):
                leaks.append(variant)
    return leaks


def secret_kinds(text: str) -> list[str]:
    """Que clases de secreto parecen estar escritas en un texto (nunca devuelve el valor).

    Args:
        text: el HTML ya normalizado.

    Returns:
        Los nombres de las clases encontradas (clave de Anthropic, hash bcrypt, JWT, token de
        Telegram); vacio si no hay ninguna.
    """
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(text)]


def normalize_tree(root: Path, repo: Path) -> None:
    """Normaliza todas las paginas de `root` y se niega si alguna deja una ruta local o un secreto.

    Args:
        root: carpeta con el HTML recien generado.
        repo: raiz del repositorio.

    Raises:
        RuntimeError: si alguna pagina conserva una ruta local o algo con forma de secreto despues
            de normalizar (el mensaje nombra la pagina y la clase, nunca el valor).

    Efectos:
        Reescribe los archivos de `root`.
    """
    for file in sorted(root.rglob("*")):
        if not file.is_file():
            continue
        data = normalize_page(file.read_bytes(), repo)
        text = data.decode("utf-8")
        page = file.relative_to(root).as_posix()
        leaks = local_path_leaks(text, repo)
        if leaks:
            raise RuntimeError(
                f"{page} conserva una ruta local ({leaks[0]}): cambia el valor de esa constante"
            )
        kinds = secret_kinds(text)
        if kinds:
            raise RuntimeError(f"{page} contiene algo con forma de secreto ({', '.join(kinds)})")
        file.write_bytes(data)


def sync_directory(source: Path, target: Path, keep: frozenset[str] = frozenset()) -> list[str]:
    """Hace que `target` tenga exactamente los archivos de `source`, tocando solo lo que cambia.

    Args:
        source: carpeta con lo nuevo.
        target: carpeta a actualizar.
        keep: nombres de archivo de `target` que no se borran aunque no esten en `source`.

    Returns:
        Las rutas relativas de lo agregado, cambiado o borrado.

    Efectos:
        Escribe y borra archivos (y carpetas vacias) en `target`.
    """
    changed: list[str] = []
    wanted = {file.relative_to(source).as_posix() for file in source.rglob("*") if file.is_file()}
    for relative in sorted(wanted):
        new = (source / relative).read_bytes()
        destination = target / relative
        if not destination.is_file() or destination.read_bytes() != new:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(new)
            changed.append(relative)
    if target.is_dir():
        for file in sorted(target.rglob("*")):
            relative = file.relative_to(target).as_posix()
            if file.is_file() and relative not in wanted and file.name not in keep:
                file.unlink()
                changed.append(relative)
        for folder in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
            if not any(folder.iterdir()):
                folder.rmdir()
    return changed


def write_reference(repo: Path) -> list[str]:
    """Regenera `docs/dev/api/` y su manifiesto.

    Args:
        repo: raiz del repositorio.

    Returns:
        Las paginas agregadas, cambiadas o borradas.

    Raises:
        RuntimeError: si pdoc falla, es de otra version o una pagina deja una ruta local.

    Efectos:
        Corre pdoc en un proceso aparte y escribe en `docs/dev/api/`.
    """
    with tempfile.TemporaryDirectory(prefix="minerva_docs_out_") as tmp:
        output = Path(tmp) / "api"
        generate_reference(repo, output)
        normalize_tree(output, repo)
        changed = sync_directory(output, repo / API_DIR, frozenset({MANIFEST_NAME}))
    write_manifest(repo)
    return changed


# --- Guias: hechos y grafo ---------------------------------------------------------------------

def guide_name(path: str) -> str:
    """El nombre de la guia (`docs/dev/modulos/NOMBRE.md`) que documenta un archivo.

    Args:
        path: ruta relativa del archivo con `/`.

    Returns:
        `frontend` para `frontend/src/...`; el paquete para el resto (`dev_docs_facts.package_of`).
    """
    if path.startswith(dev_docs_facts.FRONTEND_SRC + "/"):
        return "frontend"
    return dev_docs_facts.package_of(path)


def needs_section(path: str, repo: Path) -> bool:
    """Si un archivo debe tener su seccion en la guia (se omiten los `__init__.py` vacios).

    Args:
        path: ruta relativa del archivo con `/`.
        repo: raiz del repositorio.

    Returns:
        True salvo para un `__init__.py` sin contenido.

    Efectos:
        Lee el archivo si es un `__init__.py`.
    """
    if path.endswith("/__init__.py"):
        return bool((repo / path).read_text(encoding="utf-8").strip())
    return True


def _facts_blocks(repo: Path) -> dict[str, str]:
    """El bloque de hechos de cada archivo documentable (Python y frontend)."""
    blocks = {
        path: dev_docs_facts.render_facts(item)
        for path, item in dev_docs_facts.collect_facts(repo).items()
        if needs_section(path, repo)
    }
    blocks.update({
        path: dev_docs_facts.render_frontend_facts(item)
        for path, item in dev_docs_facts.collect_frontend_facts(repo).items()
    })
    return dict(sorted(blocks.items()))


def stub_section(path: str) -> str:
    """El borrador de la seccion de un archivo nuevo, con sus marcas de hechos vacias.

    Args:
        path: ruta relativa del archivo con `/`.

    Returns:
        El texto Markdown de la seccion.
    """
    return (
        f"\n### `{path}`\n\n{PENDING_TEXT}\n\n"
        f"<!-- hechos:{path} -->\n<!-- /hechos:{path} -->\n"
    )


def new_guide(name: str) -> str:
    """El esqueleto de una guia nueva, que el autor debe completar.

    Args:
        name: nombre de la guia (el paquete).

    Returns:
        El texto Markdown inicial.
    """
    return f"# Guía del paquete `{name}`\n\n{PENDING_TEXT}\n\n## Archivos\n"


def sync_guides(repo: Path, *, write: bool) -> list[str]:
    """Pone al dia los bloques de hechos de las guias y agrega borradores de lo que falte.

    Args:
        repo: raiz del repositorio.
        write: escribir los cambios; si es falso solo los informa.

    Returns:
        Los problemas: guias que faltan, bloques desactualizados (si no se escribe), marcas de
        archivos que ya no existen o repetidas, secciones sin escribir (con el texto pendiente).

    Efectos:
        Lee `docs/dev/modulos/*.md`; con `write`, los reescribe.
    """
    blocks = _facts_blocks(repo)
    problems: list[str] = []
    by_guide: dict[str, list[str]] = {}
    for path in blocks:
        by_guide.setdefault(guide_name(path), []).append(path)
    guides_dir = repo / GUIDES_DIR
    for name, paths in sorted(by_guide.items()):
        guide_file = guides_dir / f"{name}.md"
        label = f"{GUIDES_DIR}/{name}.md"
        if not guide_file.is_file():
            if not write:
                problems.append(f"falta la guia {label} (documenta {len(paths)} archivos)")
                continue
            guide_file.parent.mkdir(parents=True, exist_ok=True)
            guide_file.write_bytes(new_guide(name).encode("utf-8"))
        text = guide_file.read_text(encoding="utf-8")
        present = [match.group("path") for match in FACTS_MARK.finditer(text)]
        for path in sorted(set(present)):
            if present.count(path) > 1:
                problems.append(f"{label}: la seccion de {path} esta repetida")
            if path not in blocks:
                problems.append(f"{label}: cita {path}, que ya no existe")
            elif guide_name(path) != name:
                problems.append(f"{label}: {path} pertenece a la guia {guide_name(path)}.md")

        def fill(match: re.Match[str]) -> str:
            block = blocks.get(match.group("path"))
            if block is None:
                return match.group(0)
            return f"{match.group('open')}\n{block}\n{match.group('close')}"

        new_text = FACTS_MARK.sub(fill, text)
        for path in paths:
            if path not in present:
                if write:
                    new_text = new_text.rstrip("\n") + "\n" + stub_section(path)
                    new_text = FACTS_MARK.sub(fill, new_text)
                else:
                    problems.append(f"{label}: falta la seccion de {path}")
        if new_text != text:
            if write:
                guide_file.write_bytes(new_text.encode("utf-8"))
            else:
                problems.append(f"{label}: los hechos derivados del codigo estan desactualizados")
        if PENDING_TEXT in new_text:
            problems.append(f"{label}: tiene secciones {PENDING_TEXT} por escribir")
    if guides_dir.is_dir():
        for guide_file in sorted(guides_dir.glob("*.md")):
            if guide_file.stem not in by_guide:
                problems.append(f"{GUIDES_DIR}/{guide_file.name}: no corresponde a ningun paquete")
    return problems


def sync_graph(repo: Path, *, write: bool) -> list[str]:
    """Pone al dia el grafo de paquetes de `docs/dev/README.md`.

    Args:
        repo: raiz del repositorio.
        write: escribir el cambio; si es falso solo lo informa.

    Returns:
        Los problemas: falta el documento o sus marcas, o el grafo esta desactualizado.

    Efectos:
        Lee `docs/dev/README.md`; con `write`, lo reescribe.
    """
    readme = repo / DEV_README
    if not readme.is_file():
        return [f"falta {DEV_README}"]
    text = readme.read_text(encoding="utf-8")
    if not GRAPH_MARK.search(text):
        return [f"{DEV_README}: faltan las marcas del grafo (<!-- grafo-paquetes -->)"]
    graph = dev_docs_facts.render_graph(dev_docs_facts.collect_facts(repo))
    new_text = GRAPH_MARK.sub(
        lambda match: f"{match.group('open')}\n{graph}\n{match.group('close')}", text,
    )
    if new_text == text:
        return []
    if write:
        readme.write_bytes(new_text.encode("utf-8"))
        return []
    return [f"{DEV_README}: el grafo de paquetes esta desactualizado"]


def check_all(repo: Path, *, strict: bool = False) -> list[str]:
    """Todo lo desactualizado: referencia pdoc, hechos de las guias y grafo de paquetes.

    Args:
        repo: raiz del repositorio.
        strict: ademas regenera la referencia y la compara.

    Returns:
        Una descripcion por problema; vacio si todo esta al dia.

    Efectos:
        Lee fuentes, guias y referencia (ver `api_problems` para `strict`).
    """
    return api_problems(repo, strict=strict) + sync_guides(repo, write=False) + sync_graph(
        repo, write=False
    )


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada: regenera lo desactualizado o, con `--check`, solo lo verifica.

    Args:
        argv: argumentos (por defecto, los de la linea de comandos).

    Returns:
        0 si todo esta al dia (o se regenero); 1 si `--check` encuentra algo viejo o queda alguna
        seccion por escribir; 2 si pdoc falla.

    Efectos:
        Sin `--check`, escribe `docs/dev/api/`, `docs/dev/modulos/*.md` y `docs/dev/README.md`.
        Con `--worker` (uso interno) corre pdoc en este proceso.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="solo verifica; no escribe nada")
    parser.add_argument("--strict", action="store_true", help="con --check, regenera y compara")
    parser.add_argument("--force", action="store_true", help="regenera la referencia pdoc siempre")
    parser.add_argument("--worker", metavar="SALIDA", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        render_reference(Path(args.worker))
        return 0
    if args.strict and not args.check:
        parser.error("--strict solo se usa junto con --check")
    if args.check:
        problems = check_all(REPO_ROOT, strict=args.strict)
        for problem in problems:
            print(f"- {problem}")
        if not problems:
            print("La documentacion tecnica esta al dia.")
        return 1 if problems else 0
    try:
        if args.force or api_problems(REPO_ROOT):
            changed = write_reference(REPO_ROOT)
            print(f"Referencia pdoc regenerada: {len(changed)} archivos cambiados en {API_DIR}/")
        else:
            print("La referencia pdoc ya estaba al dia.")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    problems = sync_guides(REPO_ROOT, write=True) + sync_graph(REPO_ROOT, write=True)
    for problem in problems:
        print(f"- {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
