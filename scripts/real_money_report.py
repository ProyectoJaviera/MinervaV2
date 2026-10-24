"""Reporte de paso a dinero real (subfase 3.8) sobre una COPIA de la base de datos.

**Solo RECOMIENDA. v1 es 100 % paper**: este script no importa ningun backend de
ejecucion, no usa la red y nunca habilita ejecucion real; pasar a dinero real es una
decision manual tuya que requeriria una fase nueva.

Solo LEE: abre la copia en modo solo lectura (`mode=ro`) y nunca escribe en ella.
`--db` es obligatorio y debe ser una ruta distinta de `settings.database_path`
(assert explicito).

**No muestra ningun resultado salvo que `is_outcome_reveal_allowed` sea verdadero**
(gate de la medicion abierto Y registro oficial del veredicto en
`docs/VEREDICTO_3_6_REGISTRO*.txt`): con eso falso solo dice que falta. Con el
registro oficial como insumo: si no dice APORTA_VALOR concluye "no pasar a dinero
real" sin evaluar nada mas. La logica vive en `app/trading/real_money_report.py`.

Codigos de salida: 0 = informe impreso (conclusion negativa o evaluacion completa);
3 = rechazado: reveal no permitido (solo se imprime que falta); 4 = bandera de pruebas
rechazada (la base no es sintetica); 5 = `--out` rechazado porque el archivo ya existe.
Un `--out` con el nombre del registro oficial del veredicto es un error de uso (2): este
reporte no puede fabricar ese registro. `--out` solo guarda informes con conclusion
(nunca el de "falta algo").

    python scripts/real_money_report.py --db <copia consistente de la base>
    python scripts/real_money_report.py --db <copia> --log-file <data/logs/bot.log>

`--log-file` (opcional) cuenta las lineas CRITICAL "Reconciliacion fallida" del log, que
no se guardan en la base. `--allow-synthetic-for-tests`, `--frozen-hash` y `--docs-dir`
existen SOLO para las pruebas con datos sinteticos.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.trading.ai_value_report import SyntheticGuardError  # noqa: E402
from app.trading.real_money_report import (  # noqa: E402
    STATE_NOT_ALLOWED,
    build_report,
    render_report,
)
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402
from app.trading.verdict_record import RECORD_GLOB  # noqa: E402
from scripts.measurement_status import _connect_ro, format_report  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 3
EXIT_SYNTHETIC_REFUSED = 4
EXIT_OUT_EXISTS = 5


def main(
    db_path: str, *, allow_synthetic_for_tests: bool = False,
    frozen_hash: str = FROZEN_PROMPT_SHA256, docs_dir: Path | None = None,
    log_file: Path | None = None, out_path: str | None = None,
) -> int:
    """Arma y muestra el reporte de paso a dinero real de una copia; solo recomienda.

    Nunca importa un backend de ejecucion ni usa la red. Mientras `is_outcome_reveal_allowed`
    sea falso solo imprime que falta, junto con los contadores de la medicion (sin PnL).

    Args:
        db_path: copia de la base (nunca la de trabajo).
        allow_synthetic_for_tests: acepta una base sintetica (solo pruebas).
        frozen_hash: hash del prompt de la medicion; por defecto, el congelado.
        docs_dir: carpeta del registro oficial del veredicto (solo pruebas), o `None`.
        log_file: log del bot, para contar los CRITICAL de reconciliacion, o `None`.
        out_path: archivo donde guardar el informe con conclusion, o `None`; nunca se
            sobrescribe.

    Returns:
        0 informe impreso; 3 rechazado por resultados ocultos; 4 bandera de pruebas
        rechazada; 5 `out_path` ya existe.

    Efectos:
        Lee la copia con `mode=ro`, `log_file` y el registro oficial en `docs/`. Escribe
        `out_path` (modo exclusivo `x`) solo si el informe tiene conclusion. Imprime el
        informe.
    """
    if out_path is not None and Path(out_path).exists():
        print(f"RECHAZADO: {out_path} ya existe; este script no sobrescribe un informe.")
        return EXIT_OUT_EXISTS
    conn = _connect_ro(db_path)
    try:
        try:
            report = build_report(
                conn, settings=settings, frozen_hash=frozen_hash,
                allow_synthetic_for_tests=allow_synthetic_for_tests, docs_dir=docs_dir,
                log_file=log_file,
            )
        except SyntheticGuardError as exc:
            print(f"RECHAZADO: {exc}")
            return EXIT_SYNTHETIC_REFUSED
    finally:
        conn.close()

    header = f"Copia analizada: {Path(db_path).resolve()}"
    text = render_report(report, settings)
    print(header)
    print(text)
    if report.state == STATE_NOT_ALLOWED:
        # Solo contadores de la medicion (sin PnL ni resultados), como ai_value_report.
        print(format_report(report.status))
        return EXIT_REFUSED
    if out_path is not None:
        # Modo "x": falla si el archivo aparecio entre la comprobacion y ahora.
        with open(out_path, "x", encoding="utf-8") as fh:
            fh.write(header + "\n" + text)
        print(f"Informe guardado en {out_path}")
    return EXIT_OK


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        # La consola de Windows (cp1252) no imprime algunos caracteres del informe.
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    parser.add_argument(
        "--log-file", default=None,
        help="log del bot, para contar los CRITICAL de reconciliacion (opcional)",
    )
    parser.add_argument(
        "--out", default=None,
        help="guarda el informe (UTF-8) si hay conclusion; nunca sobrescribe un archivo "
        "existente ni puede llamarse como el registro oficial del veredicto",
    )
    parser.add_argument(
        "--allow-synthetic-for-tests", action="store_true",
        help="SOLO pruebas: acepta una base sintetica (se rechaza con datos reales)",
    )
    parser.add_argument(
        "--frozen-hash", default=None,
        help="SOLO pruebas, junto con --allow-synthetic-for-tests: hash de la base sintetica",
    )
    parser.add_argument(
        "--docs-dir", default=None,
        help="SOLO pruebas, junto con --allow-synthetic-for-tests: carpeta del registro oficial",
    )
    args = parser.parse_args()
    if args.frozen_hash is not None and not args.allow_synthetic_for_tests:
        parser.error("--frozen-hash solo se acepta junto con --allow-synthetic-for-tests")
    if args.docs_dir is not None and not args.allow_synthetic_for_tests:
        parser.error("--docs-dir solo se acepta junto con --allow-synthetic-for-tests")
    if args.out is not None and fnmatch.fnmatch(Path(args.out).name, RECORD_GLOB):
        parser.error(
            f"--out no puede llamarse como el registro oficial del veredicto ({RECORD_GLOB}): "
            "este reporte no fabrica ese registro"
        )
    if args.log_file is not None and not Path(args.log_file).is_file():
        parser.error(f"--log-file no existe: {args.log_file}")

    assert Path(args.db).resolve() != Path(settings.database_path).resolve(), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    sys.exit(main(
        args.db, allow_synthetic_for_tests=args.allow_synthetic_for_tests,
        frozen_hash=args.frozen_hash or FROZEN_PROMPT_SHA256,
        docs_dir=Path(args.docs_dir) if args.docs_dir else None,
        log_file=Path(args.log_file) if args.log_file else None, out_path=args.out,
    ))
