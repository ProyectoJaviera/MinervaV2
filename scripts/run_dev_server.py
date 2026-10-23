"""Servidor de desarrollo en el puerto 8001 contra una copia temporal de una
base real (pre-Fase 5, seccion 6 del plan aprobado 2026-10-14) -- NUNCA toca
el bot real del puerto 8000 ni su base de trabajo. Reemplaza el
procedimiento manual que se rehacia a mano en TAREA B/TAREA D (ver
docs/historial/PROGRESS.md y docs/FASE3_6_LLM.md) por un script reusable.

Copia `--source-db` (OBLIGATORIO -- a proposito sin default derivado de
`settings.database_path`: leer esa settings ANTES de fijar las variables de
entorno de abajo crearia el singleton `Settings()` con el entorno viejo, y
el `import app.main` de mas abajo reusaria ese mismo objeto ya cacheado en
`sys.modules`, ignorando los overrides -- pedir la ruta explicita evita todo
ese problema de orden de importacion) a un archivo nuevo en un directorio
temporal, y fija `DATABASE_PATH`/`LOG_DIR` al entorno ANTES de importar nada
de `app.*` (pydantic-settings lee el entorno al instanciar `Settings()`, que
pasa recien dentro de `uvicorn.run` -- el orden es critico, no cosmetico).

Fuerza tambien `TELEGRAM_COMMANDS_ENABLED=false` y `TELEGRAM_BOT_TOKEN=""` (un
token admite un solo consumidor de long-polling; ver `app/telegram/runtime.py`).

Fuerza `PAPER_EXECUTION_ENABLED=false` (el brazo ejecutado de la cuenta paper, subfase
3.9, solo lo activa el bot real).

Fuerza `LLM_LIVE_LABELING_ENABLED=false` (y `ANTHROPIC_API_KEY=""`, como
refuerzo redundante) SIN IMPORTAR lo que diga el `.env` real: el `.env` real
de hoy tiene ese flag en `true`. Si este script heredara el entorno real tal
cual, la instancia de desarrollo en 8001 TAMBIEN llamaria de verdad a la API
de Anthropic en cuanto el scheduler generara una sombra nueva sobre la
copia -- gasto real, no hipotetico. Este candado de seguridad esta
codificado, no es una opcion que dependa de acordarse de pasarla.

Las llamadas de red publicas que el lifespan real si hace al arrancar
(descubrimiento de universo en CoinGecko, backfill de velas en Bitunix) son
de solo lectura, sin costo y sin credenciales -- no es un riesgo de dinero,
solo de tiempo de arranque mayor al de un smoke mas acotado.

Uso:

    python scripts/run_dev_server.py --source-db data/minerva_estabilidad.db
    python scripts/run_dev_server.py --source-db data/minerva_estabilidad.db --port 8002
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _backup_database(source_path: Path, dest_path: Path) -> None:
    """Copia consistente de una base SQLite VIVA (el bot real sigue
    escribiendo en modo WAL mientras este script corre) via la API de
    backup de `sqlite3` (`Connection.backup`) -- a diferencia de
    `shutil.copy2`, que copia bytes de archivo sin coordinarse con el motor
    y podria copiar a mitad de una transaccion, dejando la copia corrupta o
    con el WAL desincronizado del archivo principal. La conexion fuente se
    abre en modo solo lectura: este script nunca escribe la base real."""
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def main(source_db: str, port: int) -> None:
    source_path = Path(source_db).resolve()
    if not source_path.exists():
        raise SystemExit(f"No existe: {source_path}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="minerva_dev_server_"))
    tmp_db = tmp_dir / "dev.db"
    # Protege contra un futuro refactor que por error pase `source_path`
    # directo como DATABASE_PATH en vez de la copia -- el servidor que
    # arranca mas abajo escribe en DATABASE_PATH, nunca debe ser la fuente.
    assert tmp_db.resolve() != source_path, (
        "la DB temporal del dev server no puede ser la fuente real"
    )
    _backup_database(source_path, tmp_db)
    print(f"DB de desarrollo (copia de {source_path}): {tmp_db}")

    log_dir = tmp_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    os.environ["DATABASE_PATH"] = str(tmp_db)
    os.environ["LOG_DIR"] = str(log_dir)
    os.environ["LLM_LIVE_LABELING_ENABLED"] = "false"
    os.environ["ANTHROPIC_API_KEY"] = ""
    # Un token de Telegram admite UN solo consumidor de getUpdates: la instancia de
    # desarrollo nunca debe competir con el bot real (candado codificado, como el del LLM).
    os.environ["TELEGRAM_COMMANDS_ENABLED"] = "false"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    # Brazo ejecutado de la cuenta paper (subfase 3.9): solo el bot real lo activa.
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    # Fase 4: ni la recoleccion de noticias (red) ni la memoria de lecciones en el dev server.
    os.environ["NEWS_COLLECTION_ENABLED"] = "false"
    os.environ["LESSONS_MEMORY_ENABLED"] = "false"
    # Fase 5: las notificaciones push de Telegram solo las envia el bot real; una instancia de
    # desarrollo sobre una copia nunca debe escribirle al chat.
    os.environ["TELEGRAM_PUSH_ENABLED"] = "false"

    import uvicorn

    print(f"Arrancando en http://127.0.0.1:{port} (Ctrl+C para detener)...")
    print("El bot real del puerto 8000 y su base de trabajo no se tocan.")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.path.insert(0, ".")

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--source-db", required=True,
        help="base real a copiar (solo se LEE, para copiar -- nunca se escribe)",
    )
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    main(args.source_db, args.port)
