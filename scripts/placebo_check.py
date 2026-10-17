"""Calibracion por placebo del criterio de valor de la IA (subfase 3.6, seccion f).

Relabela cada conglomerado al azar con la tasa de aprobacion observada y aplica
la MISMA `ai_value_verdict` (conglomerados y bootstrap), repetido `--n-repeats`
veces.

**Criterio (decision del 2026-10-17, antes de ver datos).** "Calibrado" se decide con
la tasa CONDICIONAL (frecuencia de APORTA_VALOR por azar / probabilidad de que una
repeticion llegue al punto de analisis) < 5 %, y solo si esa probabilidad es >= 50 %.
Si es menor, el resultado es NO INFORMATIVO (ni SI ni NO) y NO se corre el veredicto:
se esperan 7 dias mas de datos y se repite (docs/VEREDICTO_3_6.md). La frecuencia
bruta y su IC de Wilson se imprimen solo como informacion.

**Mismos filtros que el veredicto** (`app.trading.ai_value_report.
load_verdict_inputs`): grupos de la medicion bajo el hash congelado, sin piloto y
sin sombras no confiables (RECONCILE_FAILED / reconciliacion fallida). Lee la copia
en modo solo lectura (`mode=ro`, igual que `scripts/measurement_status.py` y
`scripts/ai_value_report.py`); `--db` debe ser una ruta distinta de
`settings.database_path` (assert explicito). El placebo no revela nada de la IA (las
etiquetas reales se sustituyen por azar), asi que puede correrse antes de que el gate
se abra; debe registrarse ANTES de la primera decision real.

**Tiempo.** Cada repeticion corre un bootstrap completo (2.000 remuestreos): ~0,65 s
con ~640 conglomerados, es decir ~11 min con las 1.000 repeticiones de diseno
(medicion y detalle en docs/VEREDICTO_3_6.md). Para una vuelta rapida, bajar
`--n-repeats` (p.ej. 100-300) a costa de un IC de Wilson mas ancho.

Al final imprime un bloque listo para copiar al registro de docs/FASE3_6_LLM.md.
`--out` guarda el informe completo (UTF-8) sin sobrescribir un archivo existente.

    python scripts/placebo_check.py --db <copia consistente de la base>
    python scripts/placebo_check.py --db <copia> --out docs/PLACEBO_REGISTRO_AAAAMMDD.txt

Codigos de salida: 0 = CALIBRADO; 6 = NO calibrado (el criterio no se usa; se revisa el
diseno); 7 = NO INFORMATIVO (probabilidad de llegar < 50 %); 3 = nada que calibrar
todavia; 4 = bandera de pruebas rechazada (la base no es sintetica); 5 = `--out`
apunta a un archivo que ya existe.

`--allow-synthetic-for-tests` y `--frozen-hash` existen SOLO para las pruebas con
datos sinteticos (misma guardia que `scripts/ai_value_report.py`).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.trading.ai_value import PLACEBO_REPEATS  # noqa: E402
from app.trading.ai_value_placebo import (  # noqa: E402
    CALIBRATED,
    NOT_INFORMATIVE,
    build_placebo,
    render_placebo,
)
from app.trading.ai_value_report import SyntheticGuardError  # noqa: E402
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402
from scripts.measurement_status import _connect_ro  # noqa: E402

EXIT_CALIBRATED = 0
EXIT_NOTHING_TO_CALIBRATE = 3
EXIT_SYNTHETIC_REFUSED = 4
EXIT_OUT_EXISTS = 5
EXIT_NOT_CALIBRATED = 6
EXIT_NOT_INFORMATIVE = 7


def main(
    db_path: str, n_repeats: int = PLACEBO_REPEATS, *, allow_synthetic_for_tests: bool = False,
    frozen_hash: str = FROZEN_PROMPT_SHA256, out_path: str | None = None,
) -> int:
    if out_path is not None and Path(out_path).exists():
        print(f"RECHAZADO: {out_path} ya existe; este script no sobrescribe un registro.")
        return EXIT_OUT_EXISTS
    conn = _connect_ro(db_path)
    try:
        print(f"Repeticiones: {n_repeats} (puede tardar; ver el docstring del script)", flush=True)
        started = time.perf_counter()
        try:
            run = build_placebo(
                conn, frozen_hash=frozen_hash, allow_synthetic_for_tests=allow_synthetic_for_tests,
                n_repeats=n_repeats,
            )
        except SyntheticGuardError as exc:
            print(f"RECHAZADO: {exc}")
            return EXIT_SYNTHETIC_REFUSED
    finally:
        conn.close()

    elapsed = time.perf_counter() - started
    header = f"Copia analizada: {Path(db_path).resolve()}"
    text = render_placebo(run, run_at=datetime.now(UTC), elapsed_s=elapsed)
    print(header)
    print(text)
    if run.result is None:
        return EXIT_NOTHING_TO_CALIBRATE
    if out_path is not None:
        # Modo "x": falla si el archivo aparecio entre la comprobacion y ahora.
        with open(out_path, "x", encoding="utf-8") as fh:
            fh.write(header + "\n" + text)
        print(f"Registro guardado en {out_path}")
    if run.calibration == CALIBRATED:
        return EXIT_CALIBRATED
    return EXIT_NOT_INFORMATIVE if run.calibration == NOT_INFORMATIVE else EXIT_NOT_CALIBRATED


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        # La consola de Windows (cp1252) no puede imprimir el delta del informe.
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--n-repeats", type=int, default=PLACEBO_REPEATS)
    parser.add_argument(
        "--out", default=None,
        help="guarda el informe (UTF-8) como registro; nunca sobrescribe un archivo existente",
    )
    parser.add_argument(
        "--allow-synthetic-for-tests", action="store_true",
        help="SOLO pruebas: acepta una base sintetica (se rechaza con datos reales)",
    )
    parser.add_argument(
        "--frozen-hash", default=None,
        help="SOLO pruebas, junto con --allow-synthetic-for-tests: hash de la base sintetica",
    )
    args = parser.parse_args()
    if args.frozen_hash is not None and not args.allow_synthetic_for_tests:
        parser.error("--frozen-hash solo se acepta junto con --allow-synthetic-for-tests")
    if args.n_repeats < 1:
        parser.error("--n-repeats debe ser >= 1")

    assert Path(args.db).resolve() != Path(settings.database_path).resolve(), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    sys.exit(main(
        args.db, args.n_repeats, allow_synthetic_for_tests=args.allow_synthetic_for_tests,
        frozen_hash=args.frozen_hash or FROZEN_PROMPT_SHA256, out_path=args.out,
    ))
