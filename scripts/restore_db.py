"""Restaura (importa) un respaldo de la base del bot en la ruta configurada (Fase 6,
docs/MANUAL_OPERACION.md): lo que se usa para MUDAR el bot a otro PC o a Docker sin perder la
medicion en curso, y para volver a un respaldo.

Con el BOT DETENIDO (nunca con el proceso escribiendo la base). Pasos de seguridad:

1. Verifica el respaldo: abre el archivo en solo lectura, `PRAGMA integrity_check` y que tenga las
   tablas de Minerva (si no, rechaza: no es una base del bot).
2. Se NIEGA a tocar un destino que ya existe, salvo `--force`; y con `--force` no lo borra: lo mueve
   a `<nombre>.previo_AAAAMMDD_HHMMSS` (con sus archivos -wal/-shm) para poder deshacer.
3. Copia con la API de backup de SQLite a un archivo temporal junto al destino, lo verifica y recien
   entonces lo pone en su sitio con un reemplazo atomico.

Imprime rutas, tamanos y la verificacion. NUNCA resultados de operaciones.

    python scripts/restore_db.py respaldo.db                  # -> data/minerva_estabilidad.db
    python scripts/restore_db.py respaldo.db --db otra.db --force

En Docker (el contenedor escribe como el usuario `minerva`, asi que el volumen queda con el dueno
correcto; el respaldo se monta en solo lectura):

    docker compose run --rm --no-deps -v "${PWD}/respaldo.db:/in/respaldo.db:ro" minerva \
        python scripts/restore_db.py /in/respaldo.db

Codigos de salida: 0 ok; 2 error de uso; 3 el respaldo no existe; 4 el respaldo no es valido;
5 el destino ya existe (usa --force); 6 no se pudo escribir el destino (la base anterior queda
como estaba).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB = "data/minerva_estabilidad.db"
REQUIRED_TABLES = frozenset({
    "system_state", "trades", "shadow_trades", "llm_logs", "signals", "ohlcv_cache",
})

EXIT_OK, EXIT_USAGE, EXIT_NO_BACKUP, EXIT_INVALID, EXIT_EXISTS = 0, 2, 3, 4, 5
EXIT_WRITE_FAILED = 6
# Mensajes de SQLite cuando no puede crear el `-shm` de un archivo WAL en un medio de solo lectura.
READ_ONLY_MEDIUM_ERRORS = ("readonly", "unable to open")


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Abre `path` en solo lectura, tambien desde un medio de solo lectura (p. ej. un montaje
    `:ro` de Docker).

    Un respaldo anterior a la Fase 7 trae el modo WAL en su cabecera y, desde un medio de solo
    lectura, `mode=ro` falla ("attempt to write a readonly database"): SQLite necesita crear el
    `-shm`. Un respaldo es un archivo suelto sin escritor, asi que en ese caso se abre
    `immutable=1`, SALVO que tenga al lado un `-wal` con contenido (entonces ese contenido importa y
    ignorarlo daria datos viejos: se deja fallar). Cualquier otro error (p. ej. `database is
    locked`) tampoco se esquiva: con un escritor presente `immutable` podria leer datos a medias."""
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # fuerza a tocar el archivo
        return conn
    except sqlite3.OperationalError as exc:
        conn.close()
        if not any(text in str(exc).lower() for text in READ_ONLY_MEDIUM_ERRORS):
            raise
        wal = path.with_name(path.name + "-wal")
        if wal.exists() and wal.stat().st_size > 0:
            raise
        return sqlite3.connect(f"{uri}&immutable=1", uri=True)
    except sqlite3.Error:
        conn.close()
        raise


def validate_backup(path: Path) -> str | None:
    """`None` si `path` es una base de Minerva integra; si no, el motivo del rechazo."""
    try:
        conn = connect_readonly(path)
    except sqlite3.OperationalError as exc:
        return f"no se pudo abrir ({type(exc).__name__})"
    except sqlite3.Error as exc:
        return f"no es una base SQLite valida ({type(exc).__name__})"
    try:
        verdict = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if verdict != "ok":
            return f"la verificacion de integridad fallo ({verdict[:80]})"
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    except sqlite3.DatabaseError as exc:
        return f"no es una base SQLite valida ({type(exc).__name__})"
    finally:
        conn.close()
    missing = sorted(REQUIRED_TABLES - tables)
    return f"no es una base de Minerva (faltan tablas: {', '.join(missing)})" if missing else None


def move_aside(destination: Path, stamp: str) -> list[tuple[Path, Path]]:
    """Mueve la base existente y sus -wal/-shm a `<nombre>.previo_<sello>`. Devuelve los pares
    (original, nuevo) para poder deshacerlo."""
    moved: list[tuple[Path, Path]] = []
    for suffix in ("", "-wal", "-shm"):
        current = destination.with_name(destination.name + suffix)
        if current.exists():
            target = destination.with_name(f"{destination.name}.previo_{stamp}{suffix}")
            os.replace(current, target)
            moved.append((current, target))
    return moved


def undo_move_aside(moved: list[tuple[Path, Path]]) -> None:
    for original, target in moved:
        if target.exists():
            os.replace(target, original)


def run(
    backup: str, db: str, force: bool = False, now: datetime | None = None,
) -> tuple[int, str]:
    source, destination = Path(backup), Path(db)
    if not source.is_file():
        return EXIT_NO_BACKUP, f"No existe el respaldo: {source}"
    if source.resolve() == destination.resolve():
        return EXIT_USAGE, "El respaldo y el destino son el mismo archivo"
    reason = validate_backup(source)
    if reason is not None:
        return EXIT_INVALID, f"Respaldo rechazado: {reason}"
    if destination.exists() and not force:
        return EXIT_EXISTS, (
            f"El destino ya existe: {destination}. Con el bot DETENIDO, repite con --force "
            "(la base actual se mueve a un archivo .previo_*, no se borra)."
        )

    lines = []
    stamp = f"{(now or datetime.now(UTC)).astimezone(UTC):%Y%m%d_%H%M%S}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".restaurando")
    temporary.unlink(missing_ok=True)
    moved: list[tuple[Path, Path]] = []
    try:
        src = connect_readonly(source)
        try:
            dst = sqlite3.connect(str(temporary))
            try:
                src.backup(dst)
                # Archivo suelto, sin -wal/-shm: el bot lo pone en WAL al conectarse.
                dst.execute("PRAGMA journal_mode=DELETE").fetchone()
            finally:
                dst.close()
        finally:
            src.close()
        reason = validate_backup(temporary)
        if reason is not None:
            temporary.unlink(missing_ok=True)
            return EXIT_INVALID, f"La copia restaurada no paso la verificacion: {reason}"
        if destination.exists():
            moved = move_aside(destination, stamp)
            lines += [f"Base anterior movida a: {target}" for _original, target in moved]
        os.replace(temporary, destination)
    except sqlite3.Error as exc:
        temporary.unlink(missing_ok=True)
        undo_move_aside(moved)
        return EXIT_INVALID, f"No se pudo copiar el respaldo ({type(exc).__name__}): {exc}"
    except OSError as exc:  # p. ej. el archivo esta en uso por otro proceso (Windows)
        temporary.unlink(missing_ok=True)
        undo_move_aside(moved)
        return EXIT_WRITE_FAILED, (
            f"No se pudo escribir el destino ({type(exc).__name__}): {exc}. "
            "La base anterior quedo como estaba. Detiene el bot e intenta de nuevo."
        )

    lines += [
        f"Restaurado: {destination} ({destination.stat().st_size / 1_048_576:.1f} MB)",
        "Verificacion de integridad: ok",
        "Arranca el bot y revisa GET /health: frozen_hash_ok debe ser true "
        "(el codigo debe ser el MISMO commit que escribio la base).",
    ]
    return EXIT_OK, "\n".join(lines)


def docker_mode_refusal(db: str) -> str | None:
    """Tras la mudanza a Docker, restaurar sobre `data/minerva_estabilidad.db` no cambia la base
    que usa el bot (scripts/docker_support.py)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.docker_support import docker_mode_refusal as refusal

    return refusal(
        db, "Restaurar aqui no cambia la base del bot: la viva esta en el volumen de Docker "
        "(docs/MUDANZA_DOCKER.md). Para volver al bot nativo: scripts\\rollback_from_docker.ps1.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("backup", help="respaldo a restaurar (hecho con scripts/backup_db.py)")
    parser.add_argument("--db", default=None, help=f"destino (def.: {DEFAULT_DB})")
    parser.add_argument("--force", action="store_true",
                        help="si el destino existe, moverlo a .previo_* y reemplazarlo")
    args = parser.parse_args(argv)
    if args.db is None:  # el destino por defecto: solo ahi aplica la proteccion de la mudanza
        refusal = docker_mode_refusal(DEFAULT_DB)
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT_USAGE
    code, message = run(args.backup, args.db or DEFAULT_DB, args.force)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
