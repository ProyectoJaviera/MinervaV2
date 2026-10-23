"""Genera `docs/ESQUEMA_BD.md`, el esquema de la base de datos (entregable de la SPEC), a partir del
DDL REAL (`app/persistence/database.py`: `SCHEMA` + migraciones), no a mano: asi no puede quedar
desactualizado, y una prueba falla si lo esta.

Crea una base en memoria con `Database.connect()` (el mismo camino que el bot) y lee de SQLite las
columnas, claves, indices y disparadores; el texto explicativo de cada tabla es el comentario SQL
(`-- ...`) que precede a su `CREATE TABLE` en `database.py`. Las tablas se agrupan por tema en
`GROUPS`; una tabla nueva sin grupo hace fallar la generacion (hay que decidir donde va).

    python scripts/export_schema_doc.py           # reescribe docs/ESQUEMA_BD.md
    python scripts/export_schema_doc.py --check   # falla si el documento esta desactualizado
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.persistence import database as database_module  # noqa: E402
from app.persistence.database import Database  # noqa: E402

OUT = REPO_ROOT / "docs" / "ESQUEMA_BD.md"

GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Cuenta paper, riesgo y control",
        "Las operaciones de la cuenta simulada y todo lo que las rodea: el estado del sistema "
        "(pausa, kill switch, breaker), los rechazos del motor de riesgo, el registro del brazo "
        "ejecutado y la auditoria de los controles.",
        ("trades", "system_state", "risk_rejections", "paper_execution_log", "control_audit"),
    ),
    (
        "Senales, sombras y medicion del LLM",
        "La medicion en vivo del valor del LLM: cada evaluacion de estrategia, las operaciones "
        "sombra que se simulan por cada senal y el registro completo de cada consulta al LLM.",
        ("signals", "signals_discarded_by_sl_cap", "shadow_trades", "llm_logs"),
    ),
    (
        "Mercado, universo y salud de las fuentes",
        "Datos de mercado en cache (velas, funding, contratos), el universo dinamico de activos y "
        "la salud de cada fuente de datos.",
        (
            "ohlcv_cache", "ohlcv_floor", "ohlcv_series_complete", "ohlcv_unrepairable_gaps",
            "funding_cache", "contract_specs_cache", "asset_universe", "data_source_health",
        ),
    ),
    (
        "Backtest (Fase 2)",
        "Resultados historicos de las estrategias por reglas. No es la medicion en vivo del LLM.",
        ("backtest_runs", "backtest_trades", "backtest_skipped_entries", "backtest_verdicts"),
    ),
    (
        "Noticias (Fase 4)",
        "Titulares recolectados (texto NO confiable), estado de cada fuente e indice Fear & Greed. "
        "Ninguna operacion depende de ellos.",
        ("news_items", "news_fetch_state", "fear_greed"),
    ),
    (
        "Memoria de lecciones (Fase 4)",
        "Operaciones cerradas convertidas en entradas de memoria, las lecciones estadisticas "
        "derivadas, su registro de aplicacion y los informes (post-mortem, calibracion). Todo lo "
        "que lleva resultados se muestra solo tras el registro oficial del veredicto.",
        ("memory_entries", "lessons", "lesson_applications", "lessons_reports"),
    ),
)

HEADER = """\
# Esquema de la base de datos

> **Generado** por `scripts/export_schema_doc.py` a partir de `app/persistence/database.py`
> (`SCHEMA` y migraciones de columnas). **No lo edites a mano**: corre
> `python scripts/export_schema_doc.py`. Una prueba (`tests/unit/test_schema_doc.py`) falla si este
> documento no coincide con el codigo.

Motor: SQLite en modo **WAL** (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`), una sola conexion
`aiosqlite` con un candado de escritura. Las fechas son texto ISO-8601 en UTC; los tiempos de velas,
enteros en milisegundos Unix. Para respaldar o mudar la base usa `scripts/docker_db.py` (con Docker)
o `scripts/backup_db.py` y `scripts/restore_db.py` (nunca copies el archivo a mano: en modo WAL
puede quedar inconsistente). Los comentarios de cada tabla vienen del codigo y citan documentos de
fase, que estan en [`docs/historial/`](historial/README.md).
"""

# Documentos de fase que se movieron a docs/historial/: los comentarios del DDL (en app/, que no se
# toca) los citan por su ruta antigua, y el documento generado los muestra con la ruta actual.
MOVED_TO_HISTORY = (
    "FASE0", "FASE2_BLOQUEO_RED", "FASE2_CRITERIOS", "FASE2_INTEGRIDAD_VELAS", "FASE2_PLAN",
    "FASE2_REEJECUCION_TABLAS", "FASE2_REEJECUCION", "FASE2_RESULTADOS", "FASE2_RIESGO_v1",
    "FASE2_RIESGO", "FASE3_PLAN", "FASE3_7_TELEGRAM", "FASE3_9_BRAZO_EJECUTADO", "FASE4_PLAN",
    "FASE5_PLAN", "FASE6_PLAN", "SPEC", "openspec", "MANUAL_OPERACION", "MIGRACION_PC",
)
_MOVED_PATH = re.compile(r"\bdocs/(" + "|".join(MOVED_TO_HISTORY) + r")\.md")


def modernize_doc_paths(text: str) -> str:
    """Sustituye la ruta antigua de un documento movido a `docs/historial/` por la actual."""
    return _MOVED_PATH.sub(r"docs/historial/\1.md", text)


def table_comments(schema: str) -> dict[str, str]:
    """El comentario SQL que precede a cada `CREATE TABLE` (lineas `--` consecutivas, sin linea en
    blanco de por medio)."""
    comments: dict[str, str] = {}
    pending: list[str] = []
    for raw in schema.splitlines():
        line = raw.strip()
        if line.startswith("--"):
            pending.append(line[2:].strip())
            continue
        match = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", line)
        if match:
            comments[match.group(1)] = " ".join(pending)
        pending = []
    return comments


def extract_checks(sql: str) -> list[str]:
    """Las expresiones `CHECK (...)` de un `CREATE TABLE`, con sus parentesis balanceados."""
    checks = []
    for match in re.finditer(r"\bCHECK\s*\(", sql):
        depth, i = 1, match.end()
        while i < len(sql) and depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        checks.append(re.sub(r"\s+", " ", sql[match.end():i - 1]).strip())
    return checks


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


async def read_schema() -> dict:
    """Tablas, columnas, indices y disparadores de una base recien creada (el camino del bot)."""
    db = Database(":memory:")
    await db.connect()
    try:
        masters = await db.fetch_all(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables: dict[str, dict] = {}
        for row in masters:
            if row["type"] != "table":
                continue
            name = row["name"]
            columns = [dict(c) for c in await db.fetch_all(f"PRAGMA table_info({name})")]
            indexes = []
            for index in await db.fetch_all(f"PRAGMA index_list({name})"):
                info = await db.fetch_all(f"PRAGMA index_info({index['name']})")
                indexes.append({
                    "name": index["name"], "unique": bool(index["unique"]),
                    "origin": index["origin"], "columns": [c["name"] for c in info],
                })
            tables[name] = {
                "sql": row["sql"], "columns": columns, "indexes": indexes,
                "foreign_keys": [dict(f) for f in await db.fetch_all(
                    f"PRAGMA foreign_key_list({name})")],
            }
        triggers = [dict(r) for r in masters if r["type"] == "trigger"]
        return {"tables": tables, "triggers": triggers}
    finally:
        await db.close()


def render(schema: dict, comments: dict[str, str]) -> str:
    tables = schema["tables"]
    grouped = [name for _t, _d, names in GROUPS for name in names]
    unknown = sorted(set(tables) - set(grouped))
    if unknown:
        raise SystemExit(
            f"Tablas sin grupo en scripts/export_schema_doc.py::GROUPS: {', '.join(unknown)}"
        )
    missing = sorted(set(grouped) - set(tables))
    if missing:
        raise SystemExit(f"GROUPS menciona tablas que ya no existen: {', '.join(missing)}")

    explicit = sum(
        1 for t in tables.values() for i in t["indexes"] if i["origin"] == "c"
    )
    out = [HEADER, f"**{len(tables)} tablas**, {explicit} indices explicitos y "
           f"{len(schema['triggers'])} disparador(es).", "", "## Indice por tema", ""]
    for title, _description, names in GROUPS:
        out.append(f"- **{title}**: " + ", ".join(f"[`{n}`](#{n})" for n in names))
    out.append("")
    for title, description, names in GROUPS:
        out += [f"## {title}", "", description, ""]
        for name in names:
            table = tables[name]
            out += [f"### {name}", ""]
            if comments.get(name):
                out += [modernize_doc_paths(comments[name]), ""]
            out += ["| Columna | Tipo | Nulo | Por defecto | Clave |", "|---|---|---|---|---|"]
            for col in table["columns"]:
                key = f"PK{col['pk']}" if col["pk"] else ""
                out.append(
                    f"| `{col['name']}` | {_md(col['type'] or '-')} | "
                    f"{'no' if col['notnull'] or col['pk'] else 'si'} | "
                    f"{_md(col['dflt_value']) if col['dflt_value'] is not None else ''} | {key} |"
                )
            out.append("")
            checks = extract_checks(table["sql"])
            if checks:
                out += ["Restricciones `CHECK`:", ""] + [f"- `{_md(c)}`" for c in checks] + [""]
            extras = [i for i in table["indexes"] if i["origin"] in ("c", "u")]
            if extras:
                out += ["Indices:", ""]
                for index in extras:
                    kind = "unico" if index["unique"] else "no unico"
                    out.append(f"- `{index['name']}` ({kind}): {', '.join(index['columns'])}")
                out.append("")
            if table["foreign_keys"]:
                out += ["Claves foraneas:", ""]
                out += [f"- `{fk['from']}` -> `{fk['table']}.{fk['to']}`"
                        for fk in table["foreign_keys"]]
                out.append("")
    if schema["triggers"]:
        out += ["## Disparadores", ""]
        for trigger in schema["triggers"]:
            out += [f"### {trigger['name']}", "", f"Sobre la tabla `{trigger['tbl_name']}`.", "",
                    "```sql", trigger["sql"].strip(), "```", ""]
    return "\n".join(out).rstrip() + "\n"


def build_document() -> str:
    return render(asyncio.run(read_schema()), table_comments(database_module.SCHEMA))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="falla si el documento esta viejo")
    args = parser.parse_args(argv)
    text = build_document()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print("docs/ESQUEMA_BD.md esta desactualizado: corre este script sin --check.",
                  file=sys.stderr)
            return 1
        print("docs/ESQUEMA_BD.md esta al dia.")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"Escrito {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
