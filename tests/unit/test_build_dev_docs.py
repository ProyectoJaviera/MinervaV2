"""Pruebas de `scripts/build_dev_docs.py`: la huella de la superficie documentada, el manifiesto,
la normalizacion del HTML de pdoc, las salvaguardas del proceso de pdoc y la sincronizacion de las
guias.

Todo con repositorios sinteticos en `tmp_path` y sin correr pdoc (eso lo hace una sola prueba, la
del repositorio real, en `test_dev_docs.py`). Nada importa `app` ni lee `.env`.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from scripts import build_dev_docs as build


def write(repo: Path, relative: str, body: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return path


SAMPLE = '''
    """Modulo."""
    import os
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from app.other import Thing

    LIMIT = 5
    """Tope."""

    def f(a, b=1):
        """Hace f."""
        return a + b

    class K:
        """Clase."""
        x = 1

        def __init__(self):
            self.y = 2
            print("hola")

        @property
        def p(self):
            """Propiedad."""
            return 1

        def m(self):
            """Metodo."""
            return 1
'''


# --- superficie documentada --------------------------------------------------------------------

def test_the_surface_ignores_function_bodies():
    base = textwrap.dedent(SAMPLE)
    digest = build.surface_digest(base)
    method_body = '"""Metodo."""\n        return 1'
    nested = 'def f(a, b=1):\n    """Hace f."""\n'
    for same in (
        base.replace("return a + b", "return a * b"),
        base.replace('print("hola")', 'print("chau")'),
        base.replace(method_body, '"""Metodo."""\n        return [i for i in range(3)]'),
        base + "\nprint('al importar')\n",                          # una llamada de modulo
        base.replace(nested, nested + "    def interna():\n        return 0\n"),
    ):
        assert same != base
        assert build.surface_digest(same) == digest


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("def f(a, b=1):", "def f(a, b=2):"),                       # un valor por defecto
        ("def f(a, b=1):", "def f(a, b=1, c=3):"),                  # un parametro nuevo
        ("Hace f.", "Hace g."),                                      # un docstring
        ("LIMIT = 5", "LIMIT = 6"),                                  # una constante
        ('"""Tope."""', '"""Otro tope."""'),                         # el docstring de una variable
        ("import os", "import sys"),                                 # un import
        ("from app.other import Thing", "from app.other import Other"),  # import bajo TYPE_CHECKING
        ("self.y = 2", "self.y = 3"),                                # atributo de __init__
        ("x = 1", "x = 2"),                                          # atributo de clase
        ("def m(self):", "async def m(self):"),                      # sync -> async
        ("@property", "@cached_property"),                           # un decorador
        ("class K:", "class K(Base):"),                              # las bases
        ('"""Clase."""', '"""Otra clase."""'),
    ],
)
def test_the_surface_changes_with_anything_pdoc_can_show(old, new):
    base = textwrap.dedent(SAMPLE)
    assert old in base
    assert build.surface_digest(base.replace(old, new)) != build.surface_digest(base)


def test_the_surface_does_not_depend_on_the_line_endings():
    base = textwrap.dedent(SAMPLE)
    assert build.surface_digest(base.replace("\n", "\r\n")) == build.surface_digest(base)


def test_the_surface_handles_one_line_stubs_and_try_import_blocks():
    source = textwrap.dedent('''
        try:
            import fast
        except ImportError:
            fast = None

        class P:
            def run(self) -> int: ...
    ''')
    surface = build.documentation_surface(source)
    assert "import fast" in surface
    assert "def run(self) -> int: ..." in surface


# --- huellas de paginas y manifiesto -----------------------------------------------------------

ENVIRONMENT = {"pdoc": "16.0.0", "python": "3.11", "plataforma": "test"}


def make_docs_repo(tmp_path: Path) -> Path:
    write(tmp_path, "app/a.py", '"""A."""\n\ndef f(x):\n    """Hace f."""\n    return x\n')
    write(tmp_path, "scripts/b.py", '"""B."""\n')
    write(tmp_path, "docs/dev/api/index.html", "<html>indice</html>\n")
    write(tmp_path, "docs/dev/api/app/a.html", "<html>a</html>\n")
    build.write_manifest(tmp_path, ENVIRONMENT)
    return tmp_path


def test_page_digests_skip_the_manifest_and_ignore_the_line_endings(tmp_path):
    repo = make_docs_repo(tmp_path)
    digests = build.page_digests(repo / build.API_DIR)
    assert sorted(digests) == ["app/a.html", "index.html"]
    (repo / "docs/dev/api/index.html").write_bytes(b"<html>indice</html>\r\n")
    assert build.page_digests(repo / build.API_DIR) == digests


def test_a_fresh_manifest_has_no_problems(tmp_path):
    repo = make_docs_repo(tmp_path)
    assert build.api_problems(repo) == []
    manifest = build.read_manifest(repo)
    assert manifest["entorno"] == ENVIRONMENT
    assert sorted(manifest["fuentes"]) == ["app/a.py", "scripts/b.py"]


def test_the_manifest_is_written_with_stable_text(tmp_path):
    repo = make_docs_repo(tmp_path)
    first = (repo / "docs/dev/api/manifest.json").read_bytes()
    build.write_manifest(repo, ENVIRONMENT)
    assert (repo / "docs/dev/api/manifest.json").read_bytes() == first
    assert first.endswith(b"\n") and b"\r" not in first


def test_a_missing_or_broken_manifest_is_reported(tmp_path):
    repo = make_docs_repo(tmp_path)
    (repo / "docs/dev/api/manifest.json").write_text("{no es json", encoding="utf-8")
    assert "falta docs/dev/api/manifest.json" in build.api_problems(repo)[0]
    (repo / "docs/dev/api/manifest.json").unlink()
    assert "falta docs/dev/api/manifest.json" in build.api_problems(repo)[0]


def test_changing_a_function_body_does_not_make_the_reference_stale(tmp_path):
    repo = make_docs_repo(tmp_path)
    write(repo, "app/a.py", '"""A."""\n\ndef f(x):\n    """Hace f."""\n    return x * 2\n')
    assert build.api_problems(repo) == []


NEW_SIGNATURE = '"""A."""\n\ndef f(x, y):\n    """Hace f."""\n    return x\n'
NEW_DOCSTRING = '"""A."""\n\ndef f(x):\n    """Hace f de otro modo."""\n    return x\n'


@pytest.mark.parametrize(
    ("relative", "body", "expected"),
    [
        ("app/a.py", NEW_SIGNATURE, "superficie documentada cambiada: app/a.py"),
        ("app/a.py", NEW_DOCSTRING, "superficie documentada cambiada: app/a.py"),
        ("app/new.py", '"""Nuevo."""\n', "archivo nuevo que no esta en el manifiesto: app/new.py"),
    ],
)
def test_a_changed_signature_docstring_or_new_file_makes_the_reference_stale(
    tmp_path, relative, body, expected,
):
    repo = make_docs_repo(tmp_path)
    write(repo, relative, body)
    problems = build.api_problems(repo)
    assert any(expected in problem for problem in problems), problems
    assert "python scripts/build_dev_docs.py" in problems[-1]


def test_a_deleted_source_is_reported(tmp_path):
    repo = make_docs_repo(tmp_path)
    (repo / "scripts/b.py").unlink()
    problems = build.api_problems(repo)
    assert "archivo borrado que sigue en el manifiesto: scripts/b.py" in problems


def test_an_edited_extra_or_missing_page_is_reported(tmp_path):
    repo = make_docs_repo(tmp_path)
    (repo / "docs/dev/api/index.html").write_text("<html>editado a mano</html>\n", encoding="utf-8")
    write(repo, "docs/dev/api/app/extra.html", "<html>sobra</html>\n")
    (repo / "docs/dev/api/app/a.html").unlink()
    problems = build.api_problems(repo)
    assert "pagina distinta a la del manifiesto: index.html" in problems
    assert "pagina sin huella en el manifiesto: app/extra.html" in problems
    assert "pagina del manifiesto que no existe: app/a.html" in problems


def test_a_syntax_error_is_reported_instead_of_crashing(tmp_path):
    repo = make_docs_repo(tmp_path)
    write(repo, "app/broken.py", "def (:\n")
    assert "no se puede parsear" in build.api_problems(repo)[0]


def test_a_manifest_of_another_format_asks_for_a_forced_rebuild(tmp_path):
    repo = make_docs_repo(tmp_path)
    manifest = build.read_manifest(repo)
    manifest["formato"] = 0
    (repo / "docs/dev/api/manifest.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8"
    )
    assert "--force" in build.api_problems(repo)[0]


def test_strict_only_compares_when_the_environment_is_the_recorded_one(tmp_path, monkeypatch):
    repo = make_docs_repo(tmp_path)
    monkeypatch.setattr(build, "current_environment", lambda: {**ENVIRONMENT, "plataforma": "otro"})
    called = []
    monkeypatch.setattr(build, "generate_reference", lambda *args: called.append(args))
    problems = build.api_problems(repo, strict=True)
    assert len(problems) == 1 and "--strict no aplica" in problems[0]
    assert called == []                                          # no regenero nada


def test_strict_compares_the_regenerated_pages_with_the_manifest(tmp_path, monkeypatch):
    repo = make_docs_repo(tmp_path)
    monkeypatch.setattr(build, "current_environment", lambda: ENVIRONMENT)

    def fake_generate(_repo, output: Path):
        write(output.parent, "api/index.html", "<html>indice</html>\n")
        write(output.parent, "api/app/a.html", "<html>a CAMBIADA</html>\n")

    monkeypatch.setattr(build, "generate_reference", fake_generate)
    problems = build.api_problems(repo, strict=True)
    assert "pagina distinta a la del manifiesto: app/a.html" in problems
    assert any("--force" in problem for problem in problems)

    def same_generate(_repo, output: Path):
        write(output.parent, "api/index.html", "<html>indice</html>\n")
        write(output.parent, "api/app/a.html", "<html>a</html>\n")

    monkeypatch.setattr(build, "generate_reference", same_generate)
    assert build.api_problems(repo, strict=True) == []


# --- normalizacion y salvaguardas --------------------------------------------------------------

def test_normalize_page_hides_the_repo_path_in_every_spelling(tmp_path):
    repo = tmp_path / "Mi Repo"
    repo.mkdir()
    posix, native = repo.as_posix(), str(repo)
    doubled = native.replace("\\", "\\\\")
    html = (
        f"WindowsPath(&#39;{posix}/docs&#39;)\r\n'{native}'\n'{doubled}'\n"
        f"PosixPath(&#39;{posix.lower()}/x&#39;)\n"
    )
    out = build.normalize_page(html.encode("utf-8"), repo).decode("utf-8")
    assert "Path(&#39;&lt;repo&gt;/docs&#39;)" in out
    assert "Path(&#39;&lt;repo&gt;/x&#39;)" in out
    assert "\r" not in out
    assert "WindowsPath" not in out and "PosixPath" not in out
    assert build.local_path_leaks(out, repo) == []


@pytest.mark.parametrize(
    "path",
    [
        "E:/Temporal/Carpeta/minerva_docs_cwd_ab12cd34",
        "/tmp/minerva_docs_cwd_x9y8",
    ],
)
def test_normalize_page_hides_the_random_working_directory(tmp_path, path):
    out = build.normalize_page(f"LAUNCH_DIR = Path(&#39;{path}&#39;)".encode(), tmp_path)
    assert out.decode() == "LAUNCH_DIR = Path(&#39;&lt;cwd&gt;&#39;)"


def test_a_leaked_home_directory_is_detected(tmp_path, monkeypatch):
    home = tmp_path / "usuarios" / "alguien"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert build.local_path_leaks(f"ruta {home.as_posix()}/datos", tmp_path / "otro") == [
        home.as_posix()
    ]
    assert build.local_path_leaks("sin rutas", tmp_path / "otro") == []


def fake_secrets() -> dict[str, str]:
    """Textos con la FORMA de un secreto, armados en tiempo de ejecucion (no son reales)."""
    return {
        "clave de Anthropic": "sk-" + "ant-" + "a" * 30,
        "hash bcrypt": "$" + "2b" + "$12$" + "a" * 53,
        "JWT": "eyJ" + "a" * 20 + "." + "b" * 12,
        "token de Telegram": "123456789:" + "A" * 35,
    }


def test_secret_kinds_name_the_class_and_never_return_the_value():
    for kind, value in fake_secrets().items():
        found = build.secret_kinds(f"valor={value}&#39;")
        assert found == [kind]
        assert value not in "".join(found)


def test_documentation_about_secrets_is_not_flagged():
    prose = "El hash empieza con $2b$ y la clave con sk-ant; un token es 123:abc; fecha 2026-10-24."
    assert build.secret_kinds(prose) == []


def test_normalize_tree_refuses_pages_with_paths_or_secrets_and_never_prints_the_value(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out"
    write(out, "ok.html", "<p>limpio</p>\n")
    build.normalize_tree(out, repo)
    assert (out / "ok.html").read_text(encoding="utf-8") == "<p>limpio</p>\n"

    value = fake_secrets()["clave de Anthropic"]
    write(out, "app/config.html", f"settings = Settings(anthropic_api_key={value!r})\n")
    with pytest.raises(RuntimeError) as error:
        build.normalize_tree(out, repo)
    message = str(error.value)
    assert "app/config.html" in message and "clave de Anthropic" in message
    assert value not in message


def test_sync_directory_touches_only_what_changes(tmp_path):
    source, target = tmp_path / "src", tmp_path / "dst"
    write(source, "a.html", "uno\n")
    write(source, "sub/b.html", "dos\n")
    write(target, "a.html", "uno\n")
    write(target, "viejo.html", "sobra\n")
    write(target, "vacia/c.html", "sobra\n")
    write(target, "manifest.json", "{}\n")

    changed = build.sync_directory(source, target, frozenset({"manifest.json"}))

    assert sorted(changed) == ["sub/b.html", "vacia/c.html", "viejo.html"]
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        "a.html", "manifest.json", "sub/b.html",
    ]
    assert not (target / "vacia").exists()
    assert build.sync_directory(source, target, frozenset({"manifest.json"})) == []


# --- el proceso de pdoc ------------------------------------------------------------------------

def test_the_pdoc_process_gets_a_clean_environment_and_never_reads_the_env_file(
    tmp_path, monkeypatch,
):
    write(tmp_path, ".env.example", "# comentario\nSECRETA=\n# ANTHROPIC_API_KEY=\nOTRA=1\n")
    write(tmp_path, ".env", "CANARIO=1\n")
    monkeypatch.setenv("SECRETA", "valor")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "valor")
    monkeypatch.setenv("MINERVA_ALGO", "valor")
    monkeypatch.setenv("CONSERVADA", "valor")
    reads: list[str] = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        reads.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)

    env = build.scrubbed_environment(tmp_path)

    assert ".env" not in reads and ".env.example" in reads
    for gone in ("SECRETA", "ANTHROPIC_API_KEY", "MINERVA_ALGO"):
        assert gone not in env
    assert env["CONSERVADA"] == "valor"
    assert env["PYTHONHASHSEED"] == "0" and env["PYTHONUTF8"] == "1"
    assert env[build.WORKER_FLAG] == "1"


def test_the_worker_refuses_to_run_outside_the_prepared_process(tmp_path, monkeypatch):
    out = tmp_path / "salida"
    monkeypatch.delenv(build.WORKER_FLAG, raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):                              # sin la marca de entorno
        build.render_reference(out)

    monkeypatch.setenv(build.WORKER_FLAG, "1")
    monkeypatch.chdir(build.REPO_ROOT)
    with pytest.raises(SystemExit):                              # dentro del repositorio
        build.render_reference(out)

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    with pytest.raises(SystemExit):                              # con un .env en el directorio
        build.render_reference(out)
    assert not out.exists()


def test_generate_reference_launches_an_isolated_process(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(build, "installed_pdoc_version", lambda: build.PDOC_VERSION)
    monkeypatch.setattr(build.subprocess, "run", fake_run)
    build.generate_reference(tmp_path, tmp_path / "salida")

    kwargs = seen["kwargs"]
    assert seen["command"][-2:] == ["--worker", str(tmp_path / "salida")]
    assert Path(kwargs["cwd"]).resolve() != build.REPO_ROOT          # directorio de trabajo vacio
    assert kwargs["env"][build.WORKER_FLAG] == "1"
    assert kwargs["creationflags"] == build.NO_WINDOW                # sin ventana de consola


def test_generate_reference_explains_a_wrong_pdoc_version_and_a_failed_process(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(build, "installed_pdoc_version", lambda: "1.0.0")
    with pytest.raises(RuntimeError, match=f"pdoc=={build.PDOC_VERSION}"):
        build.generate_reference(tmp_path, tmp_path / "salida")

    monkeypatch.setattr(build, "installed_pdoc_version", lambda: build.PDOC_VERSION)
    monkeypatch.setattr(
        build.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "se rompio todo"),
    )
    with pytest.raises(RuntimeError, match="se rompio todo"):
        build.generate_reference(tmp_path, tmp_path / "salida")


# --- guias y grafo -----------------------------------------------------------------------------

def fill_pending(repo: Path, text: str = "Texto del autor.") -> None:
    """Reemplaza el texto pendiente de todas las guias, como si el autor las hubiera escrito."""
    for guide in (repo / "docs/dev/modulos").glob("*.md"):
        guide.write_text(
            guide.read_text(encoding="utf-8").replace(build.PENDING_TEXT, text), encoding="utf-8"
        )


def make_guide_repo(tmp_path: Path) -> Path:
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/persistence/__init__.py", "")
    schema = 'SCHEMA = "CREATE TABLE IF NOT EXISTS trades (id)"\n'
    write(tmp_path, "app/persistence/database.py", schema)
    write(tmp_path, "app/trading/__init__.py", "")
    write(tmp_path, "app/trading/risk.py", '"""Riesgo."""\nX = 1\n')
    write(tmp_path, "app/trading/other.py", '"""Otro."""\nfrom app.trading import risk\n')
    write(tmp_path, "tests/unit/test_risk.py", "from app.trading import risk\n")
    return tmp_path


def test_needs_section_skips_only_empty_init_files(tmp_path):
    repo = make_guide_repo(tmp_path)
    assert not build.needs_section("app/trading/__init__.py", repo)
    assert build.needs_section("app/trading/risk.py", repo)
    write(repo, "app/trading/__init__.py", '"""Paquete."""\n')
    assert build.needs_section("app/trading/__init__.py", repo)


def test_guide_name_follows_the_package(tmp_path):
    assert build.guide_name("app/trading/risk.py") == "trading"
    assert build.guide_name("app/config.py") == "config"
    assert build.guide_name("scripts/x.py") == "scripts"
    assert build.guide_name("frontend/src/App.tsx") == "frontend"


def test_sync_guides_creates_the_guide_and_a_draft_section_for_every_file(tmp_path):
    repo = make_guide_repo(tmp_path)
    problems = build.sync_guides(repo, write=True)

    guide = (repo / "docs/dev/modulos/trading.md").read_text(encoding="utf-8")
    assert guide.startswith("# Guía del paquete `trading`")
    for path in ("app/trading/risk.py", "app/trading/other.py"):
        assert f"### `{path}`" in guide
        assert f"<!-- hechos:{path} -->" in guide and f"<!-- /hechos:{path} -->" in guide
    assert "app/trading/__init__.py" not in guide                    # vacio: sin seccion
    assert "- **Importa (internos):** `app.trading.risk`" in guide     # los hechos ya estan
    assert any("secciones _Pendiente de documentar._ por escribir" in p for p in problems)


def test_sync_guides_is_idempotent_and_keeps_the_hand_written_text(tmp_path):
    repo = make_guide_repo(tmp_path)
    build.sync_guides(repo, write=True)
    fill_pending(repo)
    guide_file = repo / "docs/dev/modulos/trading.md"
    text = guide_file.read_text(encoding="utf-8")

    assert build.sync_guides(repo, write=True) == []
    assert guide_file.read_text(encoding="utf-8") == text
    assert build.sync_guides(repo, write=False) == []


def test_sync_guides_reports_stale_facts_without_writing_in_check_mode(tmp_path):
    repo = make_guide_repo(tmp_path)
    build.sync_guides(repo, write=True)
    fill_pending(repo)
    guide_file = repo / "docs/dev/modulos/trading.md"
    text = guide_file.read_text(encoding="utf-8")
    write(repo, "app/trading/other.py", '"""Otro."""\nfrom app.persistence import database\n')

    problems = build.sync_guides(repo, write=False)

    stale = "los hechos derivados del codigo estan desactualizados"
    assert problems == [                       # `database.py` tambien gano un importador
        f"docs/dev/modulos/persistence.md: {stale}", f"docs/dev/modulos/trading.md: {stale}",
    ]
    assert guide_file.read_text(encoding="utf-8") == text            # no escribio nada


def test_sync_guides_reports_new_files_removed_files_and_misplaced_sections(tmp_path):
    repo = make_guide_repo(tmp_path)
    build.sync_guides(repo, write=True)
    fill_pending(repo)
    guide_file = repo / "docs/dev/modulos/trading.md"
    text = guide_file.read_text(encoding="utf-8")

    write(repo, "app/trading/brand_new.py", '"""Nuevo."""\n')
    (repo / "app/trading/other.py").unlink()
    foreign = "app/persistence/database.py"
    text += f"\n<!-- hechos:{foreign} -->\n<!-- /hechos:{foreign} -->\n"
    guide_file.write_text(text, encoding="utf-8")

    problems = build.sync_guides(repo, write=False)

    label = "docs/dev/modulos/trading.md"
    assert f"{label}: falta la seccion de app/trading/brand_new.py" in problems
    assert f"{label}: cita app/trading/other.py, que ya no existe" in problems
    assert f"{label}: {foreign} pertenece a la guia persistence.md" in problems
    assert not any("falta la guia" in problem for problem in problems)


def test_sync_guides_reports_a_guide_without_a_package(tmp_path):
    repo = make_guide_repo(tmp_path)
    build.sync_guides(repo, write=True)
    write(repo, "docs/dev/modulos/huerfana.md", "# Nada\n")
    assert "docs/dev/modulos/huerfana.md: no corresponde a ningun paquete" in build.sync_guides(
        repo, write=False
    )


def test_sync_graph_fills_the_marked_block_and_requires_the_marks(tmp_path):
    repo = make_guide_repo(tmp_path)
    assert build.sync_graph(repo, write=False) == ["falta docs/dev/README.md"]

    write(repo, "docs/dev/README.md", "# Indice\n")
    assert "faltan las marcas" in build.sync_graph(repo, write=False)[0]

    old = "# Indice\n<!-- grafo-paquetes -->\nviejo\n<!-- /grafo-paquetes -->\nfin\n"
    write(repo, "docs/dev/README.md", old)
    assert build.sync_graph(repo, write=False) == [
        "docs/dev/README.md: el grafo de paquetes esta desactualizado"
    ]
    assert build.sync_graph(repo, write=True) == []
    text = (repo / "docs/dev/README.md").read_text(encoding="utf-8")
    assert "```mermaid" in text and "| Paquete |" in text and text.rstrip().endswith("fin")
    assert build.sync_graph(repo, write=False) == []


# --- linea de comandos -------------------------------------------------------------------------

def test_strict_needs_check(capsys):
    with pytest.raises(SystemExit) as error:
        build.main(["--strict"])
    assert error.value.code == 2


def test_check_exits_with_1_and_lists_what_is_stale(tmp_path, monkeypatch, capsys):
    repo = make_docs_repo(tmp_path)
    monkeypatch.setattr(build, "REPO_ROOT", repo)
    monkeypatch.setattr(build, "sync_guides", lambda *args, **kwargs: [])
    monkeypatch.setattr(build, "sync_graph", lambda *args, **kwargs: [])
    assert build.main(["--check"]) == 0
    assert "esta al dia" in capsys.readouterr().out

    write(repo, "app/a.py", '"""A."""\n\ndef f(x, y):\n    """Hace f."""\n    return x\n')
    assert build.main(["--check"]) == 1
    assert "- superficie documentada cambiada: app/a.py" in capsys.readouterr().out


def test_the_worker_option_runs_pdoc_in_this_process(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(build, "render_reference", lambda output: seen.append(output))
    assert build.main(["--worker", str(tmp_path / "x")]) == 0
    assert seen == [tmp_path / "x"]
