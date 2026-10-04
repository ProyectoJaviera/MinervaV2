"""Imprime el reporte de operaciones sombra por estrategia (subfase 3.5).

Abre la base con `Database`, que aplica las migraciones de esquema si faltan:
correrlo SOBRE UNA COPIA de la base real, nunca sobre la base de trabajo:

    python scripts/shadow_report.py --db data/backups/minerva_shadow_copia.db
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.persistence.database import Database  # noqa: E402
from app.trading.shadow_report import (  # noqa: E402
    render_markdown,
    summarize_by_strategy,
    summarize_totals,
)


async def main(db_path: str) -> None:
    db = Database(db_path)
    await db.connect()
    try:
        rows = await summarize_by_strategy(db)
        totals = await summarize_totals(db)
        print(render_markdown(rows, totals))
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base a reportar")
    args = parser.parse_args()
    if not Path(args.db).exists():
        raise SystemExit(f"No existe: {args.db}")
    asyncio.run(main(args.db))
