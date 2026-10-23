"""La base del bot cuando corre en Docker: respaldar, sacar una copia y correr informes (Fase 7,
docs/MUDANZA_DOCKER.md y docs/USO.md).

Tras la mudanza la base viva esta en un volumen de Docker, no en `data/`. Estas herramientas hacen
la copia con la API de backup de SQLite DENTRO del contenedor (seguro con el bot escribiendo), la
traen al host con `docker compose cp` y VERIFICAN su SHA-256. Los informes siguen corriendo en el
host sobre esa copia, exactamente como antes: nunca tocan la base viva.

    # respaldo diario: queda en el volumen y en data/backups (los 14 mas recientes de cada lado)
    python scripts/docker_db.py backup

    # UNA copia para el procedimiento del veredicto (se imprime su ruta)
    python scripts/docker_db.py export --out-dir data/backups --prefix veredicto

    # copia fresca -> informe -> la copia se borra
    python scripts/docker_db.py run measurement_status.py
    python scripts/docker_db.py run ai_value_report.py --out docs/VEREDICTO_3_6_REGISTRO_X.txt

`run` agrega `--db <copia>` al script y le pasa el resto de los argumentos (las opciones propias de
`run`, como `--keep-copy`, van ANTES del nombre del script); sirve para todo script de `scripts/`
que acepte `--db` (measurement_status, measurement_identity, paper_execution_status, shadow_report,
lessons_report, news_status, real_money_report, ai_value_report, placebo_check). Si el bot no esta
corriendo, la copia sale de un contenedor de un solo uso sobre el mismo volumen.

Codigos de salida: 0 ok; 2 error de uso; 5 fallo de Docker o de la copia; `run` devuelve el codigo
del script que ejecuto.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)  # el Programador de tareas / `docker exec` pueden lanzarlo desde otra carpeta
sys.path.insert(0, str(REPO_ROOT))

from scripts import backup_db, docker_support  # noqa: E402

EXIT_OK, EXIT_USAGE, EXIT_DOCKER = 0, 2, 5
DEFAULT_OUT_DIR = "data/backups"
SCRIPT_NAME = re.compile(r"^[a-z][a-z0-9_]*\.py$")


def cmd_backup(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    copy = docker_support.export_db_copy(
        out_dir, args.prefix, keep_in_volume=True, volume_keep=args.keep,
    )
    print(f"Respaldo en el volumen: {docker_support.CONTAINER_BACKUP_DIR}/{copy.name}")
    size_mb = copy.stat().st_size / 1_048_576
    print(f"Copia en el host: {copy} ({size_mb:.1f} MB, SHA-256 verificado)")
    for old in backup_db.prune(out_dir, args.prefix, args.keep):
        print(f"Borrado por antiguedad (--keep {args.keep}): {old.name}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    copy = docker_support.export_db_copy(Path(args.out_dir), args.prefix)
    print(copy)  # SOLO la ruta por stdout: otros scripts la leen
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    if not SCRIPT_NAME.match(args.script):
        print(f"Nombre de script no valido: {args.script!r}", file=sys.stderr)
        return EXIT_USAGE
    script = REPO_ROOT / "scripts" / args.script
    if not script.is_file():
        print(f"No existe scripts/{args.script}", file=sys.stderr)
        return EXIT_USAGE
    forwarded = args.script_args[1:] if args.script_args[:1] == ["--"] else args.script_args
    with tempfile.TemporaryDirectory(prefix="minerva_copia_", ignore_cleanup_errors=True) as tmp:
        copy = docker_support.export_db_copy(Path(tmp), "informe")
        print(f"Copia fresca de la base de Docker: {copy.name} (se borra al terminar)",
              file=sys.stderr)
        if args.keep_copy:
            kept = Path(args.out_dir) / copy.name
            kept.parent.mkdir(parents=True, exist_ok=True)
            os.replace(copy, kept)
            copy = kept
            print(f"La copia se conserva en {kept}", file=sys.stderr)
        # Sin ventana de consola; su salida se reenvia en vivo (docker_support.run_streaming).
        return docker_support.run_streaming(
            [sys.executable, str(script), "--db", str(copy), *forwarded], cwd=REPO_ROOT,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="respaldo consistente: en el volumen y en el host")
    backup.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    backup.add_argument("--prefix", default="docker")
    backup.add_argument("--keep", type=int, default=14,
                        help="conservar los N mas recientes en el volumen y en el host (def.: 14)")
    backup.set_defaults(func=cmd_backup)

    export = sub.add_parser("export", help="UNA copia verificada en el host; imprime su ruta")
    export.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    export.add_argument("--prefix", default="docker")
    export.set_defaults(func=cmd_export)

    run = sub.add_parser("run", help="copia fresca, corre scripts/<script> --db <copia> y la borra")
    run.add_argument("script", help="nombre del script de scripts/ (p. ej. measurement_status.py)")
    run.add_argument("--keep-copy", action="store_true",
                     help="no borrar la copia: moverla a --out-dir")
    run.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                     help="donde dejar la copia con --keep-copy")
    run.add_argument("script_args", nargs=argparse.REMAINDER,
                     help="argumentos para el script (tras --)")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "keep", 0) < 0 or (
        hasattr(args, "prefix") and not re.fullmatch(r"[A-Za-z0-9_-]+", args.prefix)
    ):
        parser.error("--keep debe ser >= 0 y --prefix solo letras, numeros, '-' y '_'")
    try:
        return args.func(args)
    except docker_support.DockerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_DOCKER


if __name__ == "__main__":
    sys.exit(main())
