"""Informe del veredicto de valor de la IA (subfase 3.6, punto de analisis unico
de 300 conglomerados efectivos por lado) sobre una COPIA de la base de datos.

Solo LEE: abre la copia en modo solo lectura (URI `mode=ro`, igual que
`scripts/measurement_status.py`) y nunca escribe. `--db` es obligatorio y debe ser
una ruta distinta de `settings.database_path` (assert explicito).

**Se niega a mostrar resultados mientras `is_measurement_gate_open` sea falso**: con
el gate cerrado solo imprime contadores (los de `measurement_status`), sin PnL,
diferencia, IC ni veredicto. Tampoco los muestra si el gate abre pero, tras excluir
las sombras no confiables y el piloto, el N efectivo limpio sigue siendo menor que
el minimo. La logica vive en `app/trading/ai_value_report.py`.

Codigos de salida: 0 = veredicto impreso; 3 = rechazado por gate cerrado o punto de
analisis no alcanzado; 4 = bandera de pruebas rechazada (la base no es sintetica);
5 = `--out` apunta a un archivo que ya existe (no se sobrescribe).

Procedimiento completo (placebo antes, una sola corrida, registro de la salida):
docs/VEREDICTO_3_6.md.

    python scripts/ai_value_report.py --db <copia consistente de la base>

`--allow-synthetic-for-tests` y `--frozen-hash` existen SOLO para las pruebas con
datos sinteticos: la bandera se rechaza si alguna fila de `llm_logs` tiene el modelo
real o el hash congelado real (es decir, con cualquier copia de la base real).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.trading.ai_value_report import (  # noqa: E402
    STATE_VERDICT,
    SyntheticGuardError,
    build_report,
    render_report,
)
from app.trading.measurement_status import is_measurement_gate_open  # noqa: E402
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402
from scripts.measurement_status import _connect_ro, format_report  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 3
EXIT_SYNTHETIC_REFUSED = 4
EXIT_OUT_EXISTS = 5


def main(
    db_path: str, *, allow_synthetic_for_tests: bool = False,
    frozen_hash: str = FROZEN_PROMPT_SHA256, out_path: str | None = None,
) -> int:
    if out_path is not None and Path(out_path).exists():
        print(f"RECHAZADO: {out_path} ya existe; este script no sobrescribe un registro.")
        return EXIT_OUT_EXISTS
    conn = _connect_ro(db_path)
    try:
        try:
            result = build_report(
                conn, frozen_hash=frozen_hash, allow_synthetic_for_tests=allow_synthetic_for_tests,
            )
        except SyntheticGuardError as exc:
            print(f"RECHAZADO: {exc}")
            return EXIT_SYNTHETIC_REFUSED
    finally:
        conn.close()

    header = f"Copia analizada: {Path(db_path).resolve()}"
    text = render_report(result)
    print(header)
    print(text)
    if not is_measurement_gate_open(result.status):
        print(format_report(result.status))
    if result.state != STATE_VERDICT:
        return EXIT_REFUSED
    if out_path is not None:
        # Modo "x": falla si el archivo aparecio entre la comprobacion y ahora.
        with open(out_path, "x", encoding="utf-8") as fh:
            fh.write(header + "\n" + text)
        print(f"Registro guardado en {out_path}")
    return EXIT_OK


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        # La consola de Windows (cp1252) no puede imprimir el delta del informe.
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument(
        "--allow-synthetic-for-tests", action="store_true",
        help="SOLO pruebas: acepta una base sintetica (se rechaza con datos reales)",
    )
    parser.add_argument(
        "--frozen-hash", default=None,
        help="SOLO pruebas, junto con --allow-synthetic-for-tests: hash de la base sintetica",
    )
    parser.add_argument(
        "--out", default=None,
        help="guarda el informe (UTF-8) como registro oficial; solo si hay veredicto, "
        "y nunca sobrescribe un archivo existente",
    )
    args = parser.parse_args()
    if args.frozen_hash is not None and not args.allow_synthetic_for_tests:
        parser.error("--frozen-hash solo se acepta junto con --allow-synthetic-for-tests")

    assert Path(args.db).resolve() != Path(settings.database_path).resolve(), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    sys.exit(main(
        args.db, allow_synthetic_for_tests=args.allow_synthetic_for_tests,
        frozen_hash=args.frozen_hash or FROZEN_PROMPT_SHA256, out_path=args.out,
    ))
