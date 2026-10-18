"""Informe de la memoria de lecciones (Fase 4) sobre una COPIA de la base.

**No muestra ningun resultado salvo que `is_outcome_reveal_allowed` sea verdadero** (gate de la
medicion abierto Y registro oficial del veredicto): las lecciones llevan tasas de acierto y retornos
de operaciones cerradas. Con eso falso solo dice CUANTAS operaciones cerradas hay y que falta, y ni
siquiera abre la base para escribir (orden estricto, como `scripts/real_money_report.py`).

Con la puerta abierta construye la memoria y las lecciones EN LA COPIA (nunca en la base de trabajo:
`--db` debe ser una ruta distinta de `settings.database_path`, assert explicito) y las imprime.

Codigos de salida: 0 = informe impreso; 3 = resultados ocultos (solo se dice que falta); 4 = bandera
de pruebas rechazada (la base no es sintetica).

    python scripts/lessons_report.py --db <copia consistente de la base>

`--allow-synthetic-for-tests`, `--frozen-hash` y `--docs-dir` existen SOLO para las pruebas con
datos sinteticos.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.memory.gate import HIDDEN_RESULTS  # noqa: E402
from app.memory.report import build_report_text  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.trading.ai_value_report import SyntheticGuardError, check_frozen_hash_mode  # noqa: E402
from app.trading.measurement_status import (  # noqa: E402
    MIN_EFFECTIVE_N,
    compute_status,
    is_measurement_gate_open,
    is_outcome_reveal_allowed,
)
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402
from scripts.measurement_status import _connect_ro  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 3
EXIT_SYNTHETIC_REFUSED = 4


def main(
    db_path: str, *, allow_synthetic_for_tests: bool = False,
    frozen_hash: str = FROZEN_PROMPT_SHA256, docs_dir: Path | None = None,
) -> int:
    # 1) La puerta, con una conexion de SOLO LECTURA y sin tocar ningun resultado.
    conn = _connect_ro(db_path)
    try:
        try:
            check_frozen_hash_mode(conn, frozen_hash, allow_synthetic_for_tests)
        except SyntheticGuardError as exc:
            print(f"RECHAZADO: {exc}")
            return EXIT_SYNTHETIC_REFUSED
        status = compute_status(conn, frozen_hash=frozen_hash)
        allowed = is_outcome_reveal_allowed(status, docs_dir)
        closed = None if allowed else conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE status = 'CLOSED'"
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"Copia analizada: {Path(db_path).resolve()}")
    if not allowed:
        missing = []
        if not is_measurement_gate_open(status):
            missing.append(
                f"gate de la medicion CERRADO: APROBADA {status.n_eff_approved}/{MIN_EFFECTIVE_N}, "
                f"RECHAZADA {status.n_eff_rejected}/{MIN_EFFECTIVE_N}"
            )
        missing.append(
            "no existe el registro oficial del veredicto (docs/VEREDICTO_3_6_REGISTRO*.txt)"
        )
        print("=== Memoria de lecciones (Fase 4) ===")
        print(f"Operaciones cerradas en la base: {closed}. Lecciones, tasas de acierto y retornos: "
              f"{HIDDEN_RESULTS}.")
        print("Falta:")
        for item in missing:
            print(f"- {item}")
        return EXIT_REFUSED

    # 2) Puerta abierta: se construye la memoria en la copia y se imprime.
    async def run() -> str:
        db = Database(db_path)
        await db.connect()
        try:
            return await build_report_text(db, settings)
        finally:
            await db.close()

    print(asyncio.run(run()))
    return EXIT_OK


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument("--allow-synthetic-for-tests", action="store_true",
                        help="SOLO pruebas: acepta una base sintetica")
    parser.add_argument("--frozen-hash", default=None,
                        help="SOLO pruebas, junto con --allow-synthetic-for-tests")
    parser.add_argument("--docs-dir", default=None,
                        help="SOLO pruebas, junto con --allow-synthetic-for-tests")
    args = parser.parse_args()
    if args.frozen_hash is not None and not args.allow_synthetic_for_tests:
        parser.error("--frozen-hash solo se acepta junto con --allow-synthetic-for-tests")
    if args.docs_dir is not None and not args.allow_synthetic_for_tests:
        parser.error("--docs-dir solo se acepta junto con --allow-synthetic-for-tests")

    assert Path(args.db).resolve() != Path(settings.database_path).resolve(), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    sys.exit(main(
        args.db, allow_synthetic_for_tests=args.allow_synthetic_for_tests,
        frozen_hash=args.frozen_hash or FROZEN_PROMPT_SHA256,
        docs_dir=Path(args.docs_dir) if args.docs_dir else None,
    ))
