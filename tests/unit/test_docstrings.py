"""Guardian de los docstrings de `app/` y `scripts/`.

Todo modulo, clase y funcion o metodo PUBLICO tiene un docstring que dice para que sirve y, segun
lo que el codigo hace de verdad (se mira el AST, no el texto):

- **Args:** si la funcion recibe parametros (aparte de `self`/`cls`): una entrada por cada uno, y
  ninguna entrada de un parametro que no existe.
- **Returns:** (o **Yields:**) si devuelve un valor (`return <algo>` o `yield`).
- **Raises:** si lanza una excepcion de forma explicita (`raise X`); nombra cada clase lanzada.
- **Efectos:** si toca la base (`execute`, `fetch_*`, `*_repo.*`), la red (clientes REST,
  Telegram, `httpx`), el disco (`open`, `write_text`, ...) o crea tareas asyncio: dice QUE tablas
  lee y escribe, que servicio externo llama o que archivo toca.

Los formatos de seccion son los de Google (`Args:`, `Returns:`, `Raises:`) mas `Efectos:`, propia
del proyecto; la guia esta en `docs/dev/ENTORNO_DESARROLLO.md`. El generador de la referencia
(`scripts/build_dev_docs.py`) los convierte en HTML con pdoc.

Excepciones, todas declaradas aqui:

- `app/llm/prompts.py` y `app/llm/market_features.py` definen lo que ve el modelo y sus huellas
  estan fijadas en `tests/unit/test_prompt_v5_prepared.py`: NO se pueden editar ni para un
  comentario, asi que quedan exentos de las secciones (siguen obligados a tener docstring).
- Los metodos cuyo cuerpo es solo `...` (interfaces `Protocol`): no admiten docstring sin cambiar
  su AST; se documentan en el docstring de la clase.
- Las propiedades (`@property`) solo necesitan su docstring: sin `Args`; `Returns` no se exige.

Cada comprobador tiene su prueba con un caso que debe fallar.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("app", "scripts")

SECTION_EXEMPT_FILES = {"app/llm/prompts.py", "app/llm/market_features.py"}

# Una llamada a estos atributos es acceso a la base.
DB_ATTRIBUTES = {"execute", "executemany", "execute_many", "fetch_one", "fetch_all"}
# Receptores que son un cliente de red o de Telegram.
NETWORK_RECEIVER = re.compile(
    r"^(rest_client|rest|client|_client|_http|http|bitunix|coingecko|fetcher|ws|_ws|ws_client)$"
)
NETWORK_ATTRIBUTES = {"send_message", "get_updates", "aclose"}
FILE_ATTRIBUTES = {
    "write_text", "write_bytes", "read_text", "read_bytes", "mkdir", "unlink", "glob", "rglob",
}
FILE_FUNCTIONS = {"open"}
SIDE_EFFECT_FUNCTIONS = {"create_task"}


@dataclass(frozen=True)
class Violation:
    path: str
    qualname: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.qualname}: {self.rule}"


# --- lectura del docstring ------------------------------------------------------------------


def _section_block(doc: str, name: str) -> list[str] | None:
    """Las lineas de la seccion `name:` (indentadas bajo su encabezado), o `None` si no existe."""
    lines = doc.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"{name}:":
            base = len(line) - len(line.lstrip())
            block: list[str] = []
            for follow in lines[index + 1:]:
                if follow.strip() and (len(follow) - len(follow.lstrip())) <= base:
                    break
                block.append(follow)
            return block
    return None


def documented_arguments(doc: str) -> set[str] | None:
    """Los nombres documentados en `Args:`, o `None` si no hay seccion."""
    block = _section_block(doc, "Args")
    if block is None:
        return None
    entries = [line for line in block if line.strip()]
    if not entries:
        return set()
    top = min(len(line) - len(line.lstrip()) for line in entries)
    names: set[str] = set()
    for line in entries:
        if len(line) - len(line.lstrip()) != top:
            continue
        match = re.match(r"\s*\*{0,2}(\w+)(?:\s*\([^)]*\))?\s*:", line)
        if match:
            names.add(match.group(1))
    return names


def has_section(doc: str, *names: str) -> bool:
    return any(_section_block(doc, name) is not None for name in names)


# --- lectura del codigo -----------------------------------------------------------------------


def _own_nodes(function: ast.AST):
    """Los nodos del cuerpo de la funcion, sin entrar en funciones o clases anidadas."""
    stack = list(function.body)  # type: ignore[attr-defined]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = function.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return [n for n in names if n not in ("self", "cls")]


def returns_a_value(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in _own_nodes(function):
        if isinstance(node, ast.Yield | ast.YieldFrom):
            return True
        if isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                return True
    return False


def raised_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str] | None:
    """Clases lanzadas de forma explicita; `None` si no hay ningun `raise` con excepcion."""
    found: set[str] = set()
    any_raise = False
    for node in _own_nodes(function):
        if isinstance(node, ast.Raise) and node.exc is not None:
            any_raise = True
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name[:1].isupper():
                found.add(name)
    return found if any_raise else None


def has_side_effects(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in _own_nodes(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver = func.value
            receiver_name = (
                receiver.id if isinstance(receiver, ast.Name)
                else receiver.attr if isinstance(receiver, ast.Attribute) else ""
            )
            if func.attr in DB_ATTRIBUTES | FILE_ATTRIBUTES | NETWORK_ATTRIBUTES:
                return True
            if receiver_name.endswith("_repo") or NETWORK_RECEIVER.match(receiver_name):
                return True
            if func.attr in SIDE_EFFECT_FUNCTIONS:
                return True
        elif isinstance(func, ast.Name) and func.id in FILE_FUNCTIONS | SIDE_EFFECT_FUNCTIONS:
            return True
    return False


def is_stub(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = function.body
    return (
        len(body) == 1 and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis
    )


def is_property(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Attribute):
            name = decorator.attr
        else:
            name = getattr(decorator, "id", "")
        if name in ("property", "cached_property", "setter", "getter"):
            return True
    return False


# --- la auditoria ------------------------------------------------------------------------------


def audit_source(source: str, path: str, *, check_sections: bool = True) -> list[Violation]:
    tree = ast.parse(source)
    found: list[Violation] = []

    def report(node: ast.AST, qualname: str, rule: str) -> None:
        found.append(Violation(path, qualname, getattr(node, "lineno", 1), rule))

    if not (ast.get_docstring(tree) or "").strip():
        report(tree, "<modulo>", "falta el docstring del modulo")

    def check_function(function, qualname: str) -> None:
        doc = ast.get_docstring(function)
        if doc is None or not doc.strip():
            if is_stub(function):
                return
            report(function, qualname, "falta el docstring")
            return
        if not check_sections or is_stub(function):
            return
        prop = is_property(function)
        params = parameter_names(function)
        if params and not prop:
            documented = documented_arguments(doc)
            if documented is None:
                report(function, qualname, f"falta la seccion Args: ({', '.join(params)})")
            else:
                missing = [p for p in params if p not in documented]
                if missing:
                    report(function, qualname, f"Args: no documenta {', '.join(missing)}")
                extra = sorted(documented - set(params))
                if extra:
                    report(
                        function, qualname, f"Args: documenta lo que no existe: {', '.join(extra)}"
                    )
        if returns_a_value(function) and not prop and not has_section(doc, "Returns", "Yields"):
            report(function, qualname, "falta la seccion Returns: (devuelve un valor)")
        raised = raised_names(function)
        if raised is not None:
            block = _section_block(doc, "Raises")
            if block is None:
                report(function, qualname, "falta la seccion Raises: (lanza una excepcion)")
            else:
                text = "\n".join(block)
                absent = sorted(name for name in raised if name not in text)
                if absent:
                    report(function, qualname, f"Raises: no nombra {', '.join(absent)}")
        if has_side_effects(function) and _section_block(doc, "Efectos") is None:
            report(function, qualname, "falta la seccion Efectos: (toca base, red, disco o tareas)")

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_"):
                check_function(node, node.name)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            if not (ast.get_docstring(node) or "").strip():
                report(node, node.name, "falta el docstring de la clase")
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    if member.name.startswith("_"):
                        continue
                    check_function(member, f"{node.name}.{member.name}")
    return found


def python_files() -> list[Path]:
    files: list[Path] = []
    for root in ROOTS:
        files += sorted((REPO_ROOT / root).rglob("*.py"))
    return [f for f in files if "__pycache__" not in f.parts]


def audit_repository() -> list[Violation]:
    found: list[Violation] = []
    for path in python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_bytes().decode("utf-8")
        found += audit_source(source, relative, check_sections=relative not in SECTION_EXEMPT_FILES)
    return found


def test_every_public_item_is_documented_with_the_sections_its_code_needs():
    violations = audit_repository()

    assert not violations, (
        f"{len(violations)} docstrings incompletos. Formato en docs/dev/ENTORNO_DESARROLLO.md.\n"
        + "\n".join(str(v) for v in violations[:60])
        + ("\n..." if len(violations) > 60 else "")
    )


def test_the_files_exempt_from_the_sections_are_exactly_the_hash_pinned_ones():
    pinned_test = REPO_ROOT / "tests" / "unit" / "test_prompt_v5_prepared.py"
    pinned = pinned_test.read_text(encoding="utf-8")

    for relative in SECTION_EXEMPT_FILES:
        assert f'"{relative}"' in pinned, (
            f"{relative} ya no esta fijado por hash: quitalo de la lista"
        )


# --- el comprobador detecta cada incumplimiento -----------------------------------------------


def audit(code: str, **kwargs) -> list[str]:
    return [v.rule for v in audit_source(textwrap.dedent(code), "x.py", **kwargs)]


GOOD = '''
"""Modulo de prueba con su propio docstring."""
'''


def test_the_checker_requires_the_module_class_and_function_docstrings():
    rules = audit('''
        class Visible:
            def accion(self):
                return 1
    ''')

    assert "falta el docstring del modulo" in rules
    assert "falta el docstring de la clase" in rules
    assert "falta el docstring" in rules


def test_the_checker_ignores_private_names_and_dunder_methods():
    assert audit(GOOD + '''
class _Interna:
    def metodo(self, x):
        return x


def _ayuda(x):
    return x


class Publica:
    """Clase publica."""

    def __init__(self, x):
        self.x = x
''') == []


def test_the_checker_asks_for_args_only_when_there_are_parameters():
    rules = audit(GOOD + '''
def sin_parametros():
    """Hace algo."""


def con_parametros(a, b):
    """Hace algo."""
''')

    assert rules == ["falta la seccion Args: (a, b)"]


def test_the_checker_detects_a_missing_and_an_invented_argument():
    rules = audit(GOOD + '''
def funcion(a, b):
    """Hace algo.

    Args:
        a: el primero.
        c: no existe.
    """
''')

    assert "Args: no documenta b" in rules
    assert "Args: documenta lo que no existe: c" in rules


def test_the_checker_accepts_typed_entries_and_continuation_lines():
    assert audit(GOOD + '''
def funcion(a, *args, **kwargs):
    """Hace algo.

    Args:
        a (int): el primero,
            que sigue en otra linea.
        *args: posicionales.
        **kwargs: con nombre.
    """
''') == []


def test_the_checker_asks_for_returns_only_when_a_value_is_returned():
    rules = audit(GOOD + '''
def devuelve():
    """Hace algo."""
    return 3


def no_devuelve():
    """Hace algo."""
    return None


def genera():
    """Hace algo."""
    yield 1
''')

    assert rules == [
        "falta la seccion Returns: (devuelve un valor)",
        "falta la seccion Returns: (devuelve un valor)",
    ]


def test_the_checker_requires_raises_naming_every_raised_class():
    rules = audit(GOOD + '''
def lanza(x):
    """Hace algo.

    Args:
        x: dato.

    Raises:
        ValueError: si esta mal.
    """
    if x:
        raise ValueError("a")
    raise KeyError("b")


def lanza_sin_seccion():
    """Hace algo."""
    raise RuntimeError("c")


def relanza():
    """Hace algo."""
    try:
        pass
    except Exception:
        raise
''')

    assert rules == ["Raises: no nombra KeyError", "falta la seccion Raises: (lanza una excepcion)"]


@pytest.mark.parametrize("body", [
    "await db.execute('x')",
    "await db.fetch_all('x')",
    "await trades_repo.get_trade(db, 1)",
    "await self._repo.algo()",
    "await rest_client.get_tickers()",
    "await self.client.send_message(1, 'a')",
    "open('a')",
    "Path('a').write_text('x')",
    "asyncio.create_task(otra())",
])
def test_the_checker_requires_effects_when_the_code_touches_the_outside(body):
    rules = audit(GOOD + f'''
async def toca():
    """Hace algo."""
    {body}
''')

    assert rules == ["falta la seccion Efectos: (toca base, red, disco o tareas)"]


def test_the_checker_does_not_ask_for_effects_in_a_pure_function():
    assert audit(GOOD + '''
def pura(a):
    """Suma.

    Args:
        a: dato.

    Returns:
        El doble.
    """
    return a * 2
''') == []


def test_the_checker_only_looks_at_the_function_itself_not_its_nested_ones():
    assert audit(GOOD + '''
def externa():
    """Devuelve una funcion."""
    def interna():
        raise ValueError("solo la interna lanza")
    return None
''') == []


def test_properties_and_protocol_stubs_only_need_their_summary():
    rules = audit(GOOD + '''
class Cosa:
    """Una cosa."""

    @property
    def valor(self):
        """El valor."""
        return 1

    async def interfaz(self, *, a: int) -> int: ...
''')

    assert rules == []


def test_a_property_without_its_docstring_is_reported():
    rules = audit(GOOD + '''
class Cosa:
    """Una cosa."""

    @property
    def valor(self):
        return 1
''')

    assert rules == ["falta el docstring"]


def test_the_sections_can_be_skipped_for_the_hash_pinned_files_but_not_the_docstrings():
    code = GOOD + '''
def build(a):
    """Corta."""
    return a


def sin_doc():
    return 1
'''

    assert audit(code, check_sections=False) == ["falta el docstring"]
    assert audit(code) == [
        "falta la seccion Args: (a)", "falta la seccion Returns: (devuelve un valor)",
        "falta el docstring",
    ]
