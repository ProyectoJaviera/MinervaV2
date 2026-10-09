"""Calibracion por placebo del criterio de valor de la IA (subfase 3.6, seccion f).

Relabela cada conglomerado al azar con la tasa de aprobacion observada y aplica
la MISMA `ai_value_verdict` (conglomerados y bootstrap), repetido `--n-repeats`
veces. Si la frecuencia de `APORTA_VALOR` bajo esa relabelacion al azar es
>= 0,05, el criterio no esta bien calibrado y no se usa para decidir hasta
corregirlo.

Solo usa operaciones de la MEDICION (no el piloto, no sombras de antes de la
3.6): los mismos dos filtros que usa el reporte normal
(`app.trading.shadow_report.build_report`).

**Tiempo aproximado.** Cada repeticion corre un bootstrap completo (2.000
remuestreos por defecto). Con el valor de diseno (1.000 repeticiones) sobre el
volumen de conglomerados de una medicion ya avanzada (cientos), puede tardar
varios minutos; con unas pocas docenas de conglomerados (medicion temprana)
tarda segundos. Para una vuelta rapida, bajar `--n-repeats` (p.ej. 100) a
costa de un IC de Wilson mas ancho -- no cambia el calculo de cada repeticion,
solo cuantas se hacen.

Ejecutalo sobre una COPIA de la base (regla de CLAUDE.md, nunca la de trabajo):

    python scripts/placebo_check.py --db data/backups/minerva_placebo_copia.db
    python scripts/placebo_check.py --db data/backups/minerva_placebo_copia.db --n-repeats 100
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import llm_logs_repo, shadow_repo  # noqa: E402
from app.trading.ai_value import APROBADA, RECHAZADA, placebo_calibration  # noqa: E402


async def main(db_path: str, n_repeats: int) -> None:
    db = Database(db_path)
    await db.connect()
    try:
        trades_open = await shadow_repo.get_open(db)
        trades_closed = await shadow_repo.get_closed(db)
        all_trades = trades_open + trades_closed

        piloto_keys = await llm_logs_repo.get_piloto_signal_group_keys(db)
        measurement_keys = await llm_logs_repo.get_measurement_signal_group_keys(db)
        measured = [
            t for t in all_trades
            if t.signal_group_key in measurement_keys and t.signal_group_key not in piloto_keys
        ]
        labeled_closed = [
            t for t in measured
            if t.closed_at is not None and t.llm_decision in (APROBADA, RECHAZADA)
        ]
        if not labeled_closed:
            print("No hay operaciones de la medicion, cerradas y con decision del LLM: "
                  "nada que calibrar todavia.")
            return

        n_app = sum(1 for t in labeled_closed if t.llm_decision == APROBADA)
        approval_rate = n_app / len(labeled_closed)
        print(f"Operaciones de la medicion, cerradas y con decision: {len(labeled_closed)}")
        print(f"Tasa de aprobacion observada: {approval_rate:.1%}")
        print(f"Repeticiones: {n_repeats} (puede tardar; ver el docstring del script)")

        result = placebo_calibration(measured, approval_rate, n_repeats=n_repeats)

        print(f"APORTA_VALOR por azar: {result.aporta_valor_count} de {result.n_repeats} "
              f"({result.frequency:.1%})")
        print(f"IC 95 % de Wilson de esa frecuencia: [{result.ci_low:.1%}, {result.ci_high:.1%}]")
        print(f"Calibrado (frecuencia < 5 %): {'SI' if result.calibrated else 'NO'}")
        if not result.calibrated:
            print(
                "El criterio NO esta bien calibrado: no se usa para decidir hasta corregirlo "
                "(docs/FASE3_6_LLM.md, seccion f)."
            )
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base a analizar")
    parser.add_argument("--n-repeats", type=int, default=1000)
    args = parser.parse_args()
    if not Path(args.db).exists():
        raise SystemExit(f"No existe: {args.db}")
    assert os.path.abspath(args.db) != os.path.abspath(settings.database_path), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    asyncio.run(main(args.db, args.n_repeats))
