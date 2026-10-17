"""Estado de la recoleccion de noticias (Fase 4) sobre una COPIA de la base.

Solo LEE (`mode=ro`, como `scripts/measurement_status.py`) y exige una ruta distinta de
`settings.database_path` (assert explicito). Sin PnL ni resultados de operaciones: salud por fuente,
titulares guardados, el Fear & Greed vigente y los ultimos titulares, con la atribucion de cada
fuente (obligatoria junto a cualquier muestra de sus datos).

    python scripts/news_status.py --db <copia consistente de la base>
    python scripts/news_status.py --db <copia> --last 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.news.sentiment import label  # noqa: E402
from app.news.sources import SOURCES, attribution_lines  # noqa: E402
from scripts.measurement_status import _connect_ro  # noqa: E402


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def format_report(conn, *, last: int = 15, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    lines = ["=== Noticias recolectadas (Fase 4) ==="]
    if not _table_exists(conn, "news_items"):
        lines += ["", "No existe la tabla news_items: esta copia es anterior a la Fase 4."]
        return "\n".join(lines) + "\n"

    lines += ["", "Salud por fuente (data_source_health):"]
    health = {
        r["source"]: r for r in conn.execute(
            "SELECT source, last_success_at, last_error, consecutive_failures "
            "FROM data_source_health WHERE source LIKE 'news:%'"
        )
    }
    state = {r["source"]: r for r in conn.execute("SELECT * FROM news_fetch_state")}
    for source in SOURCES.values():
        row = health.get(source.health_key)
        if row is None:
            lines.append(f"- {source.id}: sin actividad registrada")
            continue
        nxt = state.get(source.id)
        lines.append(
            f"- {source.id}: ultimo exito {row['last_success_at'] or 'nunca'}, fallos seguidos "
            f"{row['consecutive_failures']}"
            + (f", proxima consulta {nxt['next_allowed_at']}"
               if nxt and nxt["next_allowed_at"] else "")
            + (f", ultimo error: {row['last_error']}" if row["consecutive_failures"] else "")
        )

    since = (now - timedelta(hours=24)).isoformat()
    total = conn.execute("SELECT COUNT(*) FROM news_items WHERE dup_of IS NULL").fetchone()[0]
    dups = conn.execute("SELECT COUNT(*) FROM news_items WHERE dup_of IS NOT NULL").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) AS n FROM news_items WHERE dup_of IS NULL AND fetched_at >= ? "
        "GROUP BY source", (since,),
    ).fetchall()
    lines += [
        "",
        f"Titulares guardados: {total} (mas {dups} casi duplicados marcados)",
        "Ultimas 24 h por fuente: "
        + (", ".join(f"{r['source']} {r['n']}" for r in by_source) or "ninguno"),
    ]

    if _table_exists(conn, "fear_greed"):
        fg = conn.execute("SELECT * FROM fear_greed ORDER BY ts DESC LIMIT 1").fetchone()
        lines.append(
            "Fear & Greed vigente: "
            + (f"{fg['value']} ({fg['classification']}) del {fg['ts']}" if fg else "sin datos")
            + f"  [{SOURCES['fear_greed'].attribution}]"
        )

    rows = conn.execute(
        "SELECT * FROM news_items WHERE dup_of IS NULL "
        "ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?", (last,),
    ).fetchall()
    lines += ["", f"Ultimos {len(rows)} titulares (texto de terceros, no confiable):"]
    for r in rows:
        assets = ",".join(json.loads(r["assets_json"] or "[]")) or "-"
        lines.append(
            f"- {r['published_at'] or r['fetched_at']} {r['domain'] or '-'} [{assets}] "
            f"{label(r['sentiment']) or '-'}: {r['title']}"
        )
    lines += ["", "Atribuciones:", *[f"- {a}" for a in attribution_lines()]]
    return "\n".join(lines) + "\n"


def main(db_path: str, *, last: int = 15) -> None:
    conn = _connect_ro(db_path)
    try:
        print(format_report(conn, last=last))
    finally:
        conn.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--last", type=int, default=15, help="cuantos titulares listar")
    args = parser.parse_args()

    assert os.path.abspath(args.db) != os.path.abspath(settings.database_path), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    main(args.db, last=args.last)
