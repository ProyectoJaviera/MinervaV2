"""Imprime el reporte de operaciones sombra por estrategia (subfase 3.5).

Abre la base con `Database`, que aplica las migraciones de esquema si faltan:
correrlo SOBRE UNA COPIA de la base real, nunca sobre la base de trabajo
(`--db` debe ser una ruta distinta de `settings.database_path`, assert explicito):

    python scripts/shadow_report.py --db data/backups/minerva_shadow_copia.db

**Se niega a imprimir mientras `is_outcome_reveal_allowed` sea falso** (gate abierto
Y registro oficial del veredicto en `docs/VEREDICTO_3_6_REGISTRO*.txt`): el reporte
incluye PnL por estrategia y el criterio de valor de la IA, y mirar resultados antes
del punto de analisis -- o, con el gate ya abierto, antes del placebo oficial y del
veredicto -- contaminaria la medicion (docs/FASE3_6_LLM.md seccion i y
docs/VEREDICTO_3_6.md). Para el veredicto usar `scripts/ai_value_report.py`. Codigo
de salida 3 si se niega.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.trading.ai_value import MIN_EFFECTIVE_N  # noqa: E402
from app.trading.measurement_status import (  # noqa: E402
    compute_status_async,
    is_measurement_gate_open,
    is_outcome_reveal_allowed,
)
from app.trading.shadow_report import build_report  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 3


async def main(db_path: str) -> int:
    db = Database(db_path)
    await db.connect()
    try:
        status = await compute_status_async(db)
        if not is_outcome_reveal_allowed(status):
            if is_measurement_gate_open(status):
                print(
                    "REPORTE NO MOSTRADO: gate abierto, pero todavia no existe el registro "
                    "oficial del veredicto (docs/VEREDICTO_3_6_REGISTRO*.txt). Seguir "
                    "docs/VEREDICTO_3_6.md: copia, placebo oficial y, solo si sale CALIBRADO, "
                    "scripts/ai_value_report.py. Este reporte incluye PnL por estrategia y el "
                    "criterio de valor de la IA: verlos antes contaminaria el procedimiento."
                )
            else:
                print(
                    "REPORTE NO MOSTRADO: la medicion aun no junto "
                    f"MIN_EFFECTIVE_N={MIN_EFFECTIVE_N} conglomerados efectivos por lado, y este "
                    "reporte incluye PnL por estrategia y el criterio de valor de la IA "
                    "(mirarlos antes contaminaria la medicion, seccion i).\n"
                    f"N efectivo APROBADA: {status.n_eff_approved}/{MIN_EFFECTIVE_N}, "
                    f"RECHAZADA: {status.n_eff_rejected}/{MIN_EFFECTIVE_N}."
                )
            return EXIT_REFUSED
        print(await build_report(db))
        return EXIT_OK
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base a reportar")
    args = parser.parse_args()
    if not Path(args.db).exists():
        raise SystemExit(f"No existe: {args.db}")
    assert Path(args.db).resolve() != Path(settings.database_path).resolve(), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    sys.exit(asyncio.run(main(args.db)))
