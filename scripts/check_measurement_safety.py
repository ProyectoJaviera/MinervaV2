"""Avisa si un cambio toca una zona protegida de la medicion (docs/dev/ZONAS_PROTEGIDAS.md).

Compara el arbol de trabajo (o lo que esta en el indice, o los commits desde una revision) contra
la lista de zonas protegidas (`ZONES`, la fuente unica: una prueba la mantiene alineada con el
documento) y dice CLARAMENTE que archivos protegidos cambiaron y de que manera:

- **codigo cambiado / archivo cambiado**: cambia el comportamiento; invalida o puede invalidar la
  medicion.
- **fijado por hash**: `app/llm/prompts.py` y `app/llm/market_features.py`; ni un byte (ni un
  comentario), porque una prueba los fija.
- **parametro protegido**: cambio el valor por defecto de una variable de configuracion en
  `app/config.py`, `.env.example`, `docker-compose.yml` o `scripts/start_bot.ps1`.
- **esquema**: cambio el DDL de una tabla de la medicion (`app/persistence/database.py`). Una
  columna nueva es aditiva (nivel REVISAR); cambiar o quitar lo existente es PROTEGIDO.
- **solo documentacion**: cambian docstrings, comentarios o espacios; el AST es el mismo.
- **zonas REVISAR**: no invalidan la medicion por si solas, pero pueden mostrar resultados antes
  de tiempo, detenerla, degradarla o cambiar el entorno donde corre.

Es una herramienta de LECTURA: solo ejecuta `git` y lee archivos del repositorio. No importa `app`,
no lee `.env`, no abre la base y no escribe nada. Solo ve el repositorio: un cambio hecho a mano
en tu `.env` real no se detecta (para el modelo usa `scripts/measurement_identity.py`).

    python scripts/check_measurement_safety.py                  # arbol de trabajo contra HEAD
    python scripts/check_measurement_safety.py --staged         # solo lo que entraria al commit
    python scripts/check_measurement_safety.py --since <commit> # todo lo hecho desde ese commit
    python scripts/check_measurement_safety.py --since-measurement
    python scripts/check_measurement_safety.py --list           # imprime las zonas y sale

Codigos de salida: 0 sin cambios protegidos (o solo de documentacion); 1 hay cambios PROTEGIDOS
(con `--strict`, tambien si hay algo para revisar); 2 error de uso o de git.

**Gancho pre-commit opcional** (NO se instala solo; ver docs/dev/ENTORNO_DESARROLLO.md): un
archivo `.git/hooks/pre-commit` que llame a este script con `--staged`.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.docker_support import NO_WINDOW  # noqa: E402

# El commit que cierra el congelamiento (2026-10-12 20:28 -03): prompt v4 + etiquetador en vivo +
# su registro en la documentacion. La medicion corre desde ahi. Lo usa `--since-measurement`.
MEASUREMENT_START = "c13f8ca"
WHAT_TO_DO = "docs/dev/ZONAS_PROTEGIDAS.md#si-de-verdad-hay-que-cambiar-algo-protegido"

LEVEL_PROTECTED = "PROTEGIDO"
LEVEL_REVIEW = "REVISAR"

KIND_PINNED = "fijado por hash"
KIND_CODE = "codigo cambiado"
KIND_FILE = "archivo cambiado"
KIND_ADDED = "archivo nuevo"
KIND_DELETED = "archivo borrado"
KIND_CONFIG = "parametro protegido"
KIND_CONFIG_NEW = "variable nueva en el archivo"
KIND_SCHEMA = "esquema de una tabla de la medicion"
KIND_SCHEMA_ADDED = "columna nueva (aditivo)"
KIND_RECORD = "registro oficial del veredicto creado a mano"
KIND_DOCS = "solo documentacion"

# Solo este patron abre la puerta de los resultados (`app.trading.verdict_record.RECORD_GLOB`); el
# registro del placebo (`docs/PLACEBO_<fecha>.txt`) tiene otro nombre a proposito.
VERDICT_RECORD_PATTERN = "docs/VEREDICTO_3_6_REGISTRO*.txt"
SCHEMA_FILE = "app/persistence/database.py"
CONFIG_FILE = "app/config.py"
CONFIG_CARRIERS = (".env.example", "docker-compose.yml", "scripts/start_bot.ps1")
PROTECTED_TABLES = (
    "shadow_trades", "llm_logs", "signals", "asset_universe", "signals_discarded_by_sl_cap",
)
CONSTRAINT_WORDS = ("UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "CONSTRAINT")
ABSENT = "(sin valor)"


@dataclass(frozen=True)
class Zone:
    """Una zona de la medicion que no se puede cambiar (o hay que revisar) mientras corre.

    Attributes:
        id: identificador corto y estable.
        title: titulo de la seccion de `docs/dev/ZONAS_PROTEGIDAS.md` que la explica.
        level: `PROTEGIDO` (un cambio de codigo invalida la medicion) o `REVISAR` (puede
            estropearla o degradarla sin cambiar lo que se mide).
        summary: por que, en una frase.
        files: rutas o patrones (`fnmatch`) del repositorio, con `/`.
        config: variables de entorno de la zona (cada variable pertenece a una sola zona).
        pinned: archivos de `files` fijados por hash: ni un byte puede cambiar.
        check: comprobacion especial en vez de comparar el archivo entero: `schema` (DDL de las
            tablas de la medicion) o `config` (valores por defecto de las variables).
    """

    id: str
    title: str
    level: str
    summary: str
    files: tuple[str, ...] = ()
    config: tuple[str, ...] = ()
    pinned: tuple[str, ...] = ()
    check: str = ""


ZONES: tuple[Zone, ...] = (
    Zone(
        id="prompt-y-features", title="Prompt y features", level=LEVEL_PROTECTED,
        summary="El texto y las features que ve el modelo estan congelados por hash: mezclar "
                "versiones vuelve incomparables APROBADA y RECHAZADA.",
        files=("app/llm/prompts.py", "app/llm/market_features.py",
               "tests/unit/test_prompt_v5_prepared.py"),
        pinned=("app/llm/prompts.py", "app/llm/market_features.py"),
    ),
    Zone(
        id="etiquetador-y-medicion", title="Etiquetador y medición", level=LEVEL_PROTECTED,
        summary="Como se llama al modelo, que respuesta vale y cuando se etiqueta: cambiarlo "
                "cambia que filas existen y que significan.",
        files=("app/trading/shadow_live_labeler.py", "app/llm/decision_service.py",
               "app/llm/client.py", "app/llm/_anthropic_client.py", "app/llm/schemas.py",
               "app/llm/budget.py"),
        config=("ANTHROPIC_SONNET_MODEL", "LLM_MAX_TOKENS", "LLM_TIMEOUT_SECONDS",
                "LLM_MAX_CONCURRENCY", "LLM_LIVE_LABELING_ENABLED"),
    ),
    Zone(
        id="estrategias-y-parametros", title="Estrategias y parámetros", level=LEVEL_PROTECTED,
        summary="Las seis estrategias y sus parametros estan pre-registrados: definen que "
                "senales nacen y, con la estrategia representante, los niveles de la sombra.",
        files=("app/strategies/*.py", "app/indicators/engine.py", "app/backtesting/funding.py"),
        config=("REAL_ACCOUNT_ELIGIBLE_STRATEGIES",),
    ),
    Zone(
        id="universo", title="Universo", level=LEVEL_PROTECTED,
        summary="La regla que decide que simbolos se evaluan.",
        files=("app/market/universe.py", "app/market/coingecko_client.py",
               "app/persistence/repositories/universe_repo.py"),
        config=("UNIVERSE_SIZE", "UNIVERSE_CANDIDATE_POOL", "UNIVERSE_EXCLUDE_CATEGORIES",
                "UNIVERSE_MANUAL_EXCLUSIONS", "UNIVERSE_PRICE_SANITY_TOLERANCE_PCT",
                "UNIVERSE_REFRESH_HOURS", "UNIVERSE_STALENESS_HOURS"),
    ),
    Zone(
        id="agrupacion-de-senales", title="Agrupación de señales y ciclo del scheduler",
        level=LEVEL_PROTECTED,
        summary="Como se evaluan las velas cerradas y como se agrupan las senales en una "
                "operacion sombra.",
        files=("app/trading/signal_generator.py", "app/core/scheduler.py",
               "app/market/ohlcv_history.py", "app/market/funding_history.py"),
        config=("SCHEDULER_POLL_SECONDS", "CANDLE_STALE_TOLERANCE_SECONDS",
                "FUNDING_STALE_MARGIN_HOURS"),
    ),
    Zone(
        id="niveles-sl-tp", title="Niveles de SL y TP", level=LEVEL_PROTECTED,
        summary="Donde se ponen el SL, el TP, el trailing y la liquidacion de cada sombra.",
        files=("app/trading/levels.py", "app/trading/sl_calc.py",
               "app/backtesting/liquidation.py"),
        config=("BACKTEST_FALLBACK_SL_PCT", "BACKTEST_FALLBACK_TP_PCT", "LEVERAGE"),
    ),
    Zone(
        id="tope-de-sl", title="Tope de SL", level=LEVEL_PROTECTED,
        summary="Que senales se descartan por arriesgar demasiado margen: decide que entra en "
                "la medicion.",
        files=("app/trading/signal_generator.py",),
        config=("LIVE_SL_MARGIN_CAP_PCT",),
    ),
    Zone(
        id="sombras", title="Sombras", level=LEVEL_PROTECTED,
        summary="Como se abre, se mantiene y se cierra una sombra y como se calcula su PnL: es "
                "el resultado que se mide.",
        files=("app/trading/shadow_book.py", "app/trading/fills.py", "app/trading/slippage.py",
               "app/trading/funding_accrual.py", "app/trading/stop_engine.py",
               "app/trading/position_monitor.py", "app/execution/pnl.py"),
        config=("DEFAULT_MARGIN_USDT", "TAKER_FEE_PCT", "BACKTEST_SLIPPAGE_BPS",
                "TP_GAP_TOLERANCE_PCT"),
    ),
    Zone(
        id="claves-de-la-medicion", title="Claves de la medición", level=LEVEL_PROTECTED,
        summary="Las claves unicas y los filtros que deciden que filas pertenecen a la medicion.",
        files=("app/persistence/repositories/shadow_repo.py",
               "app/persistence/repositories/llm_logs_repo.py",
               "app/persistence/repositories/signals_repo.py"),
    ),
    Zone(
        id="esquema-medicion", title="Esquema de las tablas de la medición",
        level=LEVEL_PROTECTED,
        summary="El DDL de shadow_trades, llm_logs, signals, signals_discarded_by_sl_cap, "
                "asset_universe y el disparador de inmutabilidad: agregar una columna es "
                "aditivo, cambiar lo existente no.",
        files=(SCHEMA_FILE,), check="schema",
    ),
    Zone(
        id="gate-placebo-veredicto", title="Gate, placebo y veredicto", level=LEVEL_PROTECTED,
        summary="Las reglas de decision fijadas antes de ver datos y el procedimiento de una "
                "sola mirada.",
        files=("app/trading/measurement_status.py", "app/trading/ai_value.py",
               "app/trading/ai_value_placebo.py", "app/trading/ai_value_report.py",
               "app/trading/verdict_record.py", "scripts/ai_value_report.py",
               "scripts/placebo_check.py"),
    ),
    Zone(
        id="ocultamiento", title="Ocultamiento de resultados", level=LEVEL_PROTECTED,
        summary="La puerta que impide ver PnL antes del registro oficial y las listas blancas "
                "que lo aplican.",
        files=("app/trading/reveal.py", "app/memory/gate.py", "app/api/serializers.py"),
    ),
    Zone(
        id="parametros", title="Parámetros de configuración protegidos", level=LEVEL_PROTECTED,
        summary="Los valores por defecto de las variables protegidas, donde esten escritos.",
        files=(CONFIG_FILE, *CONFIG_CARRIERS), check="config",
    ),
    Zone(
        id="cuenta-paper", title="Cuenta paper y brazo ejecutado", level=LEVEL_REVIEW,
        summary="No cambia lo que se mide, pero cambia las reglas con que se evaluara el brazo "
                "ejecutado (criterios de la subfase 3.8): si cambian a mitad, la muestra mezcla "
                "dos reglas.",
        files=("app/trading/risk_engine.py", "app/trading/paper_execution.py",
               "app/execution/paper_backend.py"),
        config=("MAX_SIMULTANEOUS_POSITIONS", "MAX_SAME_DIRECTION_POSITIONS",
                "MAX_DRAWDOWN_PCT", "MAX_DAILY_LOSS_PCT", "MAX_CAPITAL_PCT_PER_ASSET",
                "CONSECUTIVE_LOSSES_CIRCUIT_BREAKER", "CIRCUIT_BREAKER_COOLDOWN_HOURS",
                "DRAWDOWN_STOP_MODE", "INITIAL_CAPITAL_USDT", "LLM_REAL_MAX_DELAY_SECONDS",
                "PAPER_EXECUTION_ENABLED", "AUTO_OPEN_WITHOUT_LLM", "MIN_PAPER_TRADING_DAYS",
                "MIN_CLOSED_TRADES", "MIN_PROFIT_FACTOR"),
    ),
    Zone(
        id="operativos", title="Parámetros operativos", level=LEVEL_REVIEW,
        summary="Presupuesto, precios y tiempos del monitor: no cambian el significado de lo "
                "medido, pero un valor malo puede dejar grupos en SIN_LLM o retrasar cierres.",
        config=("LLM_DAILY_BUDGET_USD", "LLM_PRICE_INPUT_PER_MTOK", "LLM_PRICE_OUTPUT_PER_MTOK",
                "MONITOR_TICK_SECONDS", "TICK_STALE_SECONDS", "WS_STALE_AFTER_SECONDS",
                "REPORT_TIMEZONE"),
    ),
    Zone(
        id="datos-de-mercado", title="Fuentes de datos de mercado", level=LEVEL_REVIEW,
        summary="Un arreglo aparentemente inocente puede cambiar en silencio las velas, el "
                "funding o los ticks que ven las estrategias y el monitor.",
        files=("app/market/bitunix_rest.py", "app/market/bitunix_ws.py",
               "app/market/contract_specs.py"),
    ),
    Zone(
        id="canales", title="Canales que muestran datos de operaciones", level=LEVEL_REVIEW,
        summary="Rutas de la API, Telegram e informes leen la base: una linea de mas puede "
                "mostrar resultados antes de tiempo.",
        files=("app/api/routes/*.py", "app/telegram/commands.py", "app/telegram/push.py",
               "app/trading/shadow_report.py", "app/trading/real_money_report.py",
               "scripts/shadow_report.py", "scripts/lessons_report.py",
               "scripts/real_money_report.py", "scripts/telegram_daily.py",
               "frontend/src/SummaryTab.tsx"),
    ),
    Zone(
        id="controles", title="Controles que detienen la medición", level=LEVEL_REVIEW,
        summary="Pausar o activar el kill switch detiene la medicion: la advertencia y la "
                "confirmacion no pueden relajarse.",
        files=("app/trading/control_state.py", "app/trading/control_actions.py",
               "app/api/routes/control.py"),
    ),
    Zone(
        id="arranque", title="Arranque del proceso", level=LEVEL_REVIEW,
        summary="El cableado del lifespan crea el etiquetador, el scheduler y el monitor: un "
                "error aqui puede dejar la medicion parada sin que nada falle.",
        files=("app/main.py",),
    ),
    Zone(
        id="entorno", title="Entorno de ejecución", level=LEVEL_REVIEW,
        summary="Las dependencias exactas y la imagen donde corre el bot: un cambio en pandas o "
                "pydantic puede alterar indicadores o validaciones sin que nada lo avise.",
        files=(
            "pyproject.toml", "docker/requirements.lock", "docker/requirements.old-cpu.lock",
            "Dockerfile",
        ),
    ),
    Zone(
        id="prompt-v5", title="Prompt v5 preparado", level=LEVEL_REVIEW,
        summary="Esta preparado para la proxima medicion y nada puede importarlo: editarlo no "
                "cambia la medicion actual, importarlo desde cualquier parte la invalidaria.",
        files=("app/llm/prompts_v5.py",),
    ),
    Zone(
        id="guardianes", title="Pruebas guardianas", level=LEVEL_REVIEW,
        summary="Debilitar una de estas pruebas quita una proteccion: un cambio aqui exige "
                "explicar por que ya no hace falta.",
        files=("tests/e2e/test_no_leaks.py", "tests/e2e/leaks.py",
               "tests/unit/test_outcome_reveal.py",
               "tests/unit/test_paper_account_results_hidden.py",
               "tests/unit/test_serializers.py", "tests/unit/test_llm_prompts.py",
               "tests/unit/test_llm_market_features.py",
               "tests/unit/test_shadow_live_labeler.py"),
    ),
)


@dataclass(frozen=True)
class Finding:
    """Un hallazgo: un archivo o un parametro protegido que cambio.

    Attributes:
        level: nivel del hallazgo (`PROTEGIDO` o `REVISAR`).
        zone: titulo de la zona (o de las zonas, separadas por ` / `).
        path: archivo (o `archivo: VARIABLE` para un parametro).
        kind: de que manera cambio (`KIND_*`).
        detail: texto extra (valor anterior y nuevo, columnas, etc.).
    """

    level: str
    zone: str
    path: str
    kind: str
    detail: str = ""

    @property
    def blocks(self) -> bool:
        """Si el hallazgo cuenta como un cambio que invalida o puede invalidar la medicion."""
        return self.kind != KIND_DOCS


def config_owners() -> dict[str, Zone]:
    """La zona a la que pertenece cada variable de entorno protegida o a revisar.

    Returns:
        `VARIABLE -> zona`.
    """
    return {name: zone for zone in ZONES for name in zone.config}


def zones_for(path: str) -> list[Zone]:
    """Las zonas a las que pertenece un archivo.

    Args:
        path: ruta relativa a la raiz del repositorio, con `/`.

    Returns:
        Las zonas cuyo patron coincide; un archivo puede estar en mas de una.
    """
    return [zone for zone in ZONES if any(fnmatch.fnmatchcase(path, p) for p in zone.files)]


def is_verdict_record(path: str) -> bool:
    """Si la ruta es la de un registro oficial del veredicto (el que abre la puerta).

    Args:
        path: ruta relativa a la raiz del repositorio, con `/`.

    Returns:
        True para `docs/VEREDICTO_3_6_REGISTRO*.txt`.
    """
    return fnmatch.fnmatchcase(path, VERDICT_RECORD_PATTERN)


def ascii_fold(text: str) -> str:
    """El texto sin acentos ni caracteres fuera de ASCII (la consola de Windows no es UTF-8).

    Args:
        text: el texto.

    Returns:
        El mismo texto con las letras acentuadas sin acento y lo demas fuera de ASCII quitado.
    """
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def same_value(text: str, default: str) -> bool:
    """Si el valor escrito en un archivo de ejemplo es el valor por defecto de `Settings`.

    Args:
        text: el valor tal como esta escrito (`10`, `true`, `claude-x`).
        default: el valor por defecto como texto de codigo (`10`, `False`, `'claude-x'`).

    Returns:
        True si coinciden ignorando comillas y mayusculas, o si son el mismo numero.
    """
    left, right = text.strip().strip("\"'"), default.strip().strip("\"'")
    if left.lower() == right.lower():
        return True
    try:
        return float(left) == float(right)
    except ValueError:
        return False


def normalize(text: str) -> str:
    """El texto con saltos de linea LF, para comparar sin importar el sistema operativo.

    Args:
        text: el texto.

    Returns:
        El mismo texto con `\\n` en lugar de `\\r\\n`.
    """
    return text.replace("\r\n", "\n")


def _strip_docstrings(tree: ast.AST) -> None:
    """Quita de un arbol sintactico los docstrings de modulo, clases y funciones."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            rest = node.body[1:]
            # Un modulo puede quedar vacio (igual que un `__init__.py` sin nada); una clase o una
            # funcion necesita al menos una sentencia, y `pass` es lo que escribiria quien la deja
            # sin docstring.
            node.body = rest if isinstance(node, ast.Module) else rest or [ast.Pass()]


def code_signature(text: str) -> str:
    """El arbol sintactico de un codigo sin docstrings, como texto comparable.

    Dos versiones con la misma firma solo difieren en docstrings, comentarios o espacios.

    Args:
        text: codigo fuente de Python.

    Returns:
        El volcado del arbol.

    Raises:
        SyntaxError: si el codigo no se puede parsear.
    """
    tree = ast.parse(text)
    _strip_docstrings(tree)
    return ast.dump(tree)


def classify_change(path: str, old: str | None, new: str | None, *, pinned: bool) -> str | None:
    """De que manera cambio un archivo entre dos versiones.

    Args:
        path: ruta del archivo (solo su extension importa).
        old: contenido anterior, o `None` si no existia.
        new: contenido nuevo, o `None` si ya no existe.
        pinned: si esta fijado por hash (ni un byte puede cambiar).

    Returns:
        `None` si no cambio; si no, uno de `KIND_ADDED`, `KIND_DELETED`, `KIND_PINNED`,
        `KIND_CODE`, `KIND_FILE` (no es Python) o `KIND_DOCS`.
    """
    if old is None and new is None:
        return None
    if old is None:
        return KIND_ADDED
    if new is None:
        return KIND_DELETED
    old, new = normalize(old), normalize(new)
    if old == new:
        return None
    if pinned:
        return KIND_PINNED
    if not path.endswith(".py"):
        return KIND_FILE
    try:
        return KIND_DOCS if code_signature(old) == code_signature(new) else KIND_CODE
    except SyntaxError:
        return KIND_CODE


def config_defaults(text: str) -> dict[str, str]:
    """El valor por defecto de cada campo de `Settings`, por nombre de variable de entorno.

    Args:
        text: el codigo de `app/config.py`.

    Returns:
        `VARIABLE -> valor por defecto`, como texto del codigo (p. ej. `10` o `'claude-x'`).
        Vacio si no se puede parsear o no hay clase `Settings`.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    found: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "Settings"):
            continue
        for item in node.body:
            if not (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)):
                continue
            name, default = item.target.id.upper(), ABSENT
            if isinstance(item.value, ast.Call):
                for keyword in item.value.keywords:
                    if keyword.arg == "alias" and isinstance(keyword.value, ast.Constant):
                        name = str(keyword.value.value)
                    if keyword.arg == "default":
                        default = ast.unparse(keyword.value)
            elif item.value is not None:
                default = ast.unparse(item.value)
            found[name] = default
    return found


def carrier_values(text: str, names: frozenset[str]) -> dict[str, list[str]]:
    """Las lineas de un archivo de configuracion que nombran una variable.

    Sirve para `.env.example`, `docker-compose.yml` y `scripts/start_bot.ps1`.

    Args:
        text: contenido del archivo.
        names: variables a buscar.

    Returns:
        `VARIABLE -> lineas (sin espacios sobrantes)` donde aparece como palabra completa; las
        lineas de comentario (`#`) y los bloques de ayuda `<# ... #>` de PowerShell se ignoran.
    """
    found: dict[str, list[str]] = {}
    in_block = False
    for line in normalize(text).split("\n"):
        stripped = line.strip()
        if in_block:
            in_block = "#>" not in stripped
            continue
        if stripped.startswith("<#"):
            in_block = "#>" not in stripped
            continue
        if stripped.startswith("#"):
            continue
        for name in names:
            if re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", stripped):
                found.setdefault(name, []).append(stripped)
    return found


def config_changes(
    path: str, old: str | None, new: str | None, names: frozenset[str],
) -> list[tuple[str, str, str]]:
    """Que variables cambiaron de valor por defecto en un archivo.

    Args:
        path: `app/config.py` o uno de `CONFIG_CARRIERS`.
        old: contenido anterior, o `None` si no existia.
        new: contenido nuevo, o `None` si ya no existe.
        names: variables a vigilar.

    Returns:
        Tuplas `(variable, valor anterior, valor nuevo)`; `(sin valor)` si no estaba definida.
    """
    if path == CONFIG_FILE:
        before = config_defaults(old or "")
        after = config_defaults(new or "")
        return [
            (name, before.get(name, ABSENT), after.get(name, ABSENT))
            for name in sorted(names) if before.get(name) != after.get(name)
        ]
    before_lines = carrier_values(old or "", names)
    after_lines = carrier_values(new or "", names)
    return [
        (
            name,
            "; ".join(before_lines.get(name, [ABSENT])),
            "; ".join(after_lines.get(name, [ABSENT])),
        )
        for name in sorted(names) if before_lines.get(name) != after_lines.get(name)
    ]


def _squash(text: str) -> str:
    """Colapsa los espacios y saltos de linea de un fragmento de SQL."""
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", text)).strip()


def split_top_level(body: str) -> list[str]:
    """Parte el cuerpo de un `CREATE TABLE` por las comas que no estan entre parentesis.

    Args:
        body: el texto entre el parentesis que abre y el que cierra la tabla.

    Returns:
        Las definiciones (columnas y restricciones), con los espacios colapsados.
    """
    parts: list[str] = []
    current: list[str] = []
    depth, quote = 0, ""
    for char in re.sub(r"--[^\n]*", "", body):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [_squash(part) for part in parts if part.strip()]


def table_columns(body: str) -> dict[str, str]:
    """Las columnas de una tabla (`nombre -> definicion`) y sus restricciones (`@texto`).

    Args:
        body: el cuerpo del `CREATE TABLE`.

    Returns:
        Las columnas por nombre; cada restriccion de tabla (`UNIQUE (...)`, `CHECK ...`) con la
        clave `@` seguida de su texto.
    """
    columns: dict[str, str] = {}
    for part in split_top_level(body):
        name, _, rest = part.partition(" ")
        if name.upper() in CONSTRAINT_WORDS:
            columns["@" + part] = ""
        else:
            columns[name] = rest
    return columns


def migrated_columns(text: str) -> dict[str, dict[str, str]]:
    """Las columnas agregadas por migracion (`_<TABLA>_MIGRATED_COLUMNS`) de las tablas vigiladas.

    Args:
        text: el codigo de `app/persistence/database.py`.

    Returns:
        `tabla -> {columna: definicion}`; vacio si el codigo no se puede parsear.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    found: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        for table in PROTECTED_TABLES:
            if node.targets[0].id != f"_{table.upper()}_MIGRATED_COLUMNS":
                continue
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            found[table] = {str(k): _squash(str(v)) for k, v in value.items()}
    return found


def protected_schema(text: str) -> dict[str, dict[str, str]]:
    """El esquema vigilado: columnas de las tablas de la medicion y cuerpo de sus disparadores.

    Args:
        text: el codigo de `app/persistence/database.py`.

    Returns:
        `tabla -> {columna: definicion}` (las columnas de `CREATE TABLE` mas las de migracion) y
        `disparador <nombre> -> {"@": cuerpo}`.
    """
    flat = normalize(text)
    schema: dict[str, dict[str, str]] = {}
    for table in PROTECTED_TABLES:
        match = re.search(
            rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", flat, flags=re.DOTALL
        )
        if match:
            schema[table] = table_columns(match.group(1))
    for trigger in re.finditer(
        r"CREATE TRIGGER IF NOT EXISTS (\w+)(.*?)END;", flat, flags=re.DOTALL
    ):
        schema[f"disparador {trigger.group(1)}"] = {"@": _squash(trigger.group(2))}
    for table, columns in migrated_columns(flat).items():
        for name, definition in columns.items():
            schema.setdefault(table, {}).setdefault(name, definition)
    return schema


def schema_changes(old: str | None, new: str | None) -> list[tuple[bool, str]]:
    """Que cambio en el esquema vigilado entre dos versiones de `database.py`.

    Args:
        old: contenido anterior, o `None` si no existia.
        new: contenido nuevo, o `None` si ya no existe.

    Returns:
        Tuplas `(aditivo, descripcion)`: `aditivo` es True solo para una columna nueva; una
        columna eliminada o con otra definicion, una restriccion nueva o cambiada, una tabla o un
        disparador que aparece o desaparece son cambios de lo existente.
    """
    before, after = protected_schema(old or ""), protected_schema(new or "")
    changes: list[tuple[bool, str]] = []
    for table in sorted(set(before) | set(after)):
        if table not in before or table not in after:
            state = "nueva" if table not in before else "desaparecida"
            changes.append((False, f"{table}: definicion {state}"))
            continue
        for key in sorted(set(before[table]) | set(after[table])):
            if before[table].get(key) == after[table].get(key):
                continue
            if key.startswith("@"):
                changes.append((False, f"{table}: restriccion o cuerpo cambiado"))
            elif key not in before[table]:
                changes.append((True, f"{table}.{key}"))
            elif key not in after[table]:
                changes.append((False, f"{table}.{key}: columna eliminada"))
            else:
                changes.append((False, f"{table}.{key}: definicion cambiada"))
    return changes


def _config_findings(
    path: str, old: str | None, new: str | None, defaults: dict[str, str],
) -> list[Finding]:
    """Los hallazgos de un archivo de configuracion cuyo valor de una variable cambio.

    En `.env.example` solo importa que el ejemplo ya no coincida con el defecto de `Settings`; en
    `app/config.py`, que cambie el defecto; en `docker-compose.yml` y `scripts/start_bot.ps1`, que
    cambie o desaparezca una linea que fija la variable (una linea nueva solo se avisa).
    """
    owners = config_owners()
    findings: list[Finding] = []
    for name, before, after in config_changes(path, old, new, frozenset(owners)):
        zone = owners[name]
        if path == ".env.example":
            default = defaults.get(name)
            written = after.rpartition("=")[2]
            if after == ABSENT or default is None or same_value(written, default):
                continue
            detail = f"el ejemplo dice {written} y el defecto de Settings es {default}"
            findings.append(Finding(zone.level, zone.title, f"{path}: {name}", KIND_CONFIG, detail))
            continue
        introduced = before == ABSENT and path != CONFIG_FILE
        findings.append(Finding(
            LEVEL_REVIEW if introduced else zone.level, zone.title, f"{path}: {name}",
            KIND_CONFIG_NEW if introduced else KIND_CONFIG, f"antes {before} -> ahora {after}",
        ))
    return findings


def evaluate_file(
    path: str, old: str | None, new: str | None, defaults: dict[str, str] | None = None,
) -> list[Finding]:
    """Los hallazgos de un archivo que cambio.

    Args:
        path: ruta relativa a la raiz del repositorio, con `/`.
        old: contenido anterior, o `None` si no existia.
        new: contenido nuevo, o `None` si ya no existe.
        defaults: valores por defecto vigentes de `Settings` (`config_defaults` del
            `app/config.py` nuevo); solo hacen falta para comparar `.env.example`.

    Returns:
        Los hallazgos; vacio si el archivo no esta en ninguna zona o no cambio en lo que importa.
    """
    if is_verdict_record(path):
        if old == new:
            return []
        return [Finding(
            LEVEL_PROTECTED, "Gate, placebo y veredicto", path, KIND_RECORD,
            "lo escribe scripts/ai_value_report.py --out; crearlo a mano abre la puerta de los "
            "resultados sin veredicto",
        )]
    zones = zones_for(path)
    if not zones:
        return []
    title = " / ".join(zone.title for zone in zones)
    checks = {zone.check for zone in zones if zone.check}
    if "config" in checks:
        return _config_findings(path, old, new, defaults or {})
    if "schema" in checks:
        changes = schema_changes(old, new)
        findings = []
        existing = [text for additive, text in changes if not additive]
        added = [text for additive, text in changes if additive]
        if existing:
            findings.append(Finding(
                LEVEL_PROTECTED, title, path, KIND_SCHEMA, "; ".join(existing),
            ))
        if added:
            findings.append(Finding(
                LEVEL_REVIEW, title, path, KIND_SCHEMA_ADDED, "; ".join(added),
            ))
        return findings
    pinned = any(fnmatch.fnmatchcase(path, p) for zone in zones for p in zone.pinned)
    kind = classify_change(path, old, new, pinned=pinned)
    if kind is None:
        return []
    level = LEVEL_PROTECTED if any(z.level == LEVEL_PROTECTED for z in zones) else LEVEL_REVIEW
    return [Finding(level, title, path, kind)]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta `git` sobre `repo` sin ventana de consola; no lanza si git falla."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=NO_WINDOW, check=False,
    )


def _show(repo: Path, revision: str, path: str) -> str | None:
    """El contenido de `path` en una revision (`""` para el indice), o `None` si no existe."""
    done = _git(repo, "show", f"{revision}:{path}")
    return done.stdout if done.returncode == 0 else None


def changed_paths(repo: Path, base: str, *, staged: bool) -> dict[str, str]:
    """Los archivos que cambiaron respecto de una revision.

    Args:
        repo: raiz del repositorio.
        base: la revision de comparacion (`HEAD`, un commit...).
        staged: comparar solo lo que esta en el indice (lo que entraria al commit).

    Returns:
        `ruta -> estado` (`A`, `M` o `D`); los renombrados cuentan como borrado mas alta. Cuando
        no es `staged` incluye los archivos nuevos sin seguimiento (no ignorados).

    Raises:
        RuntimeError: si git falla (no es un repositorio, la revision no existe...).

    Efectos:
        Ejecuta `git diff` y `git ls-files`; no escribe nada.
    """
    args = ["diff", "--no-renames", "--name-status"]
    args += ["--cached", base] if staged else [base]
    done = _git(repo, *args)
    if done.returncode != 0:
        raise RuntimeError(f"git diff fallo: {done.stderr.strip() or done.stdout.strip()}")
    changes: dict[str, str] = {}
    for line in done.stdout.splitlines():
        status, _, path = line.partition("\t")
        if path:
            changes[path.strip()] = status.strip()[:1]
    if not staged:
        untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
        for path in untracked.stdout.splitlines():
            changes.setdefault(path.strip(), "A")
    return changes


def gather_findings(repo: Path, base: str, *, staged: bool) -> list[Finding]:
    """Compara el repositorio con una revision y devuelve todos los hallazgos.

    Args:
        repo: raiz del repositorio.
        base: la revision de comparacion.
        staged: comparar solo el indice.

    Returns:
        Los hallazgos, ordenados por nivel, zona y ruta.

    Raises:
        RuntimeError: si git falla.

    Efectos:
        Ejecuta `git` y lee archivos del repositorio; no escribe nada.
    """

    def current(path: str, status: str = "M") -> str | None:
        if status == "D":
            return None
        if staged:
            return _show(repo, "", path)
        file = repo / path
        return file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None

    findings: list[Finding] = []
    defaults: dict[str, str] | None = None
    for path, status in sorted(changed_paths(repo, base, staged=staged).items()):
        if not (zones_for(path) or is_verdict_record(path)):
            continue
        if defaults is None and path == ".env.example":
            defaults = config_defaults(current(CONFIG_FILE) or "")
        findings += evaluate_file(path, _show(repo, base, path), current(path, status), defaults)
    order = {LEVEL_PROTECTED: 0, LEVEL_REVIEW: 1}
    return sorted(findings, key=lambda f: (order[f.level], f.zone, f.path))


def commits_touching(repo: Path, base: str, paths: list[str], limit: int = 25) -> list[str]:
    """Los commits desde `base` que tocaron alguno de esos archivos.

    Args:
        repo: raiz del repositorio.
        base: revision de partida (excluida).
        paths: archivos a mirar.
        limit: cuantos commits como maximo.

    Returns:
        Lineas `hash fecha asunto`, del mas reciente al mas antiguo.

    Efectos:
        Ejecuta `git log`; no escribe nada.
    """
    if not paths:
        return []
    done = _git(
        repo, "log", f"--max-count={limit}", "--date=short", "--format=%h %ad %s",
        f"{base}..HEAD", "--", *paths,
    )
    return done.stdout.splitlines() if done.returncode == 0 else []


def render(findings: list[Finding], header: str, commits: list[str]) -> str:
    """El informe en texto, con lo protegido primero y lo que solo es documentacion al final.

    Args:
        findings: los hallazgos.
        header: la linea que dice que se comparo.
        commits: los commits que tocaron archivos protegidos (puede ser vacio).

    Returns:
        El texto del informe.
    """
    lines = [header, ""]
    blocking = [f for f in findings if f.blocks and f.level == LEVEL_PROTECTED]
    review = [f for f in findings if f.blocks and f.level == LEVEL_REVIEW]
    docs_only = [f for f in findings if not f.blocks]
    if blocking:
        lines.append("PROTEGIDO -- cambia lo que se mide o como se mide:")
        for f in blocking:
            extra = f" ({f.detail})" if f.detail else ""
            lines.append(f"  * {f.path}  [{f.kind}]{extra}  -- {f.zone}")
    if review:
        lines += ["", "PARA REVISAR -- no invalida por si solo, pero puede estropear la medicion:"]
        for f in review:
            extra = f" ({f.detail})" if f.detail else ""
            lines.append(f"  * {f.path}  [{f.kind}]{extra}  -- {f.zone}")
    if docs_only:
        lines += ["", "Solo documentacion (el AST es el mismo; no cambia el comportamiento):"]
        for f in docs_only:
            lines.append(f"  * {f.path}  -- {f.zone}")
    if commits:
        lines += ["", "Commits que tocaron archivos protegidos:"]
        lines += [f"  {c}" for c in commits]
    if not (blocking or review or docs_only):
        lines.append("Sin cambios en zonas protegidas.")
    else:
        lines += [
            "",
            f"Resumen: {len(blocking)} protegidos, {len(review)} para revisar, "
            f"{len(docs_only)} solo documentacion.",
        ]
        if blocking:
            lines.append("Que hacer: " + WHAT_TO_DO)
    return ascii_fold("\n".join(lines) + "\n")


def list_zones() -> str:
    """Las zonas, una por bloque, con sus archivos y variables.

    Returns:
        El texto que imprime `--list`.
    """
    lines = []
    for zone in ZONES:
        lines.append(f"[{zone.level}] {zone.title} ({zone.id})")
        lines.append(f"    {zone.summary}")
        for pattern in zone.files:
            note = "  (fijado por hash)" if pattern in zone.pinned else ""
            lines.append(f"    archivo: {pattern}{note}")
        if zone.config:
            lines.append("    variables: " + ", ".join(zone.config))
    lines.append("[PROTEGIDO] Registro oficial del veredicto")
    lines.append(f"    archivo: {VERDICT_RECORD_PATTERN}")
    return ascii_fold("\n".join(lines) + "\n")


def main(argv: list[str] | None = None, repo: Path = REPO_ROOT) -> int:
    """Linea de comandos: compara el repositorio con la lista de zonas protegidas.

    Args:
        argv: argumentos (por defecto, los de la linea de comandos).
        repo: raiz del repositorio (solo cambia en las pruebas).

    Returns:
        0 sin cambios protegidos; 1 con cambios protegidos (o para revisar, con `--strict`);
        2 si git falla.

    Efectos:
        Ejecuta `git` y lee archivos del repositorio; imprime el informe. No escribe nada.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--staged", action="store_true", help="solo lo que esta en el indice")
    parser.add_argument("--since", metavar="REV", help="compara con esa revision")
    parser.add_argument(
        "--since-measurement", action="store_true",
        help=f"compara con el inicio de la medicion ({MEASUREMENT_START})",
    )
    parser.add_argument("--strict", action="store_true", help="tambien falla si hay algo a revisar")
    parser.add_argument("--list", action="store_true", help="imprime las zonas y sale")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.list:
        print(list_zones(), end="")
        return 0
    if args.since and args.since_measurement:
        parser.error("--since y --since-measurement son excluyentes")
    if args.staged and (args.since or args.since_measurement):
        parser.error("--staged compara con HEAD: no se combina con --since ni --since-measurement")
    base = args.since or (MEASUREMENT_START if args.since_measurement else "HEAD")
    try:
        findings = gather_findings(repo, base, staged=args.staged)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    what = "el indice (lo que entraria al commit)" if args.staged else "el arbol de trabajo"
    header = f"Zonas protegidas de la medicion: {what} contra {base}"
    touched = [f.path.split(":")[0] for f in findings if f.blocks]
    commits = commits_touching(repo, base, touched) if base != "HEAD" else []
    print(render(findings, header, commits), end="")
    protected = any(f.blocks and f.level == LEVEL_PROTECTED for f in findings)
    review = any(f.blocks and f.level == LEVEL_REVIEW for f in findings)
    return 1 if protected or (args.strict and review) else 0


if __name__ == "__main__":
    sys.exit(main())
