"""Levanta A MANO el stop por drawdown (modo `duro`) del motor de riesgo (Fase 6,
docs/USO.md).

El stop por drawdown bloquea las entradas nuevas de la cuenta paper cuando el equity cae
`MAX_DRAWDOWN_PCT` (20 % por defecto) desde su pico, y NUNCA se levanta solo ni desde Telegram o el
dashboard: es la senal de que algo anda mal y debe mirarlo una persona. Hasta ahora los avisos
decian "se levanta aparte (`resume_drawdown_stop`)" pero no habia como ejecutarlo; este script es
esa accion.

- **Sin `--confirm` solo MUESTRA** el estado y lo que haria. No escribe nada.
- **Con `--confirm` solo actua si el stop esta ACTIVO**; si no lo esta, no hace nada (codigo 3).
- Reinicia el pico de equity al equity actual (`risk_engine.resume_drawdown_stop`): asi el stop
  puede volver a activarse con una caida NUEVA. NO reinicia el contador
  `drawdown_stop_would_have_triggered_count` (criterio de paso a dinero real).
- El equity es el capital inicial mas el PnL realizado: las posiciones abiertas no cuentan porque
  aqui no hay precios de mercado. `--equity X` fija otro valor.
- **Nunca imprime saldo ni equity mientras la puerta de resultados este cerrada** (regla del
  2026-10-17): solo dice que lo hizo. Con la puerta abierta SI muestra el valor usado.

Se puede correr con el bot en marcha (solo escribe unas claves de `system_state`, que el motor de
riesgo vuelve a leer en cada decision); no hace falta reiniciarlo.

    python scripts/resume_drawdown_stop.py                    # estado, sin escribir
    python scripts/resume_drawdown_stop.py --confirm          # levantarlo
    docker compose exec minerva python scripts/resume_drawdown_stop.py --confirm

Codigos de salida: 0 ok; 2 error de uso; 3 el stop no esta activo (nada que levantar);
4 la base no existe.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

# El Programador de tareas / `docker exec` pueden arrancar en otro directorio: se fija la raiz.
os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.persistence.repositories import system_state_repo  # noqa: E402
from app.trading import risk_engine  # noqa: E402
from app.trading.reveal import reveal_allowed  # noqa: E402
from scripts.docker_support import docker_mode_refusal  # noqa: E402

DEFAULT_DB = "data/minerva_estabilidad.db"
EXIT_OK, EXIT_USAGE, EXIT_NOT_ACTIVE, EXIT_NO_DB = 0, 2, 3, 4
STOP_KEY = "drawdown_stop_active"


def read_stop_state(db_path: Path) -> tuple[bool, str | None]:
    """Lee el estado del stop por drawdown en SOLO LECTURA.

    El modo sin `--confirm` no escribe nada, ni siquiera las migraciones de
    `Database.connect()`.

    Args:
        db_path: base del bot.

    Returns:
        La tupla `(stop activo, cuando se levanto por ultima vez)`; la fecha es `None` si
        nunca se levanto.

    Efectos:
        Lee `system_state` (`drawdown_stop_active` y `drawdown_stop_last_resumed_at`) con
        `mode=ro`.
    """
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = dict(conn.execute(
            "SELECT key, value FROM system_state WHERE key IN (?, ?)",
            (STOP_KEY, "drawdown_stop_last_resumed_at"),
        ))
    finally:
        conn.close()
    return rows.get(STOP_KEY) == "1", rows.get("drawdown_stop_last_resumed_at")


async def realized_equity(db: Database, initial_capital: float) -> float:
    """El equity realizado: capital inicial mas el PnL neto de las operaciones cerradas.

    Las posiciones abiertas no cuentan porque el script no tiene precios de mercado.

    Args:
        db: conexion a la base.
        initial_capital: capital inicial, en USDT.

    Returns:
        El equity, en USDT.

    Efectos:
        Lee `trades` (las cerradas).
    """
    row = await db.fetch_one(
        "SELECT COALESCE(SUM(pnl_net_usdt), 0) AS pnl FROM trades WHERE status = 'CLOSED'"
    )
    return initial_capital + float(row["pnl"])


async def run(
    db_path: str, confirm: bool, equity: float | None = None,
    initial_capital: float | None = None,
) -> tuple[int, str]:
    """Muestra el estado del stop por drawdown y, con `confirm`, lo levanta.

    Sin `confirm` no escribe nada. Con `confirm` solo actua si el stop esta activo: reinicia
    el pico de equity al equity usado, pero no el contador del criterio de dinero real.
    Mientras los resultados estan ocultos no imprime el equity.

    Args:
        db_path: base del bot.
        confirm: levantar el stop (si no, solo informa).
        equity: equity a usar como nuevo pico; por defecto, capital inicial mas PnL realizado.
        initial_capital: capital inicial; por defecto `settings.initial_capital_usdt`.

    Returns:
        La tupla `(codigo de salida, mensaje)`: 0 ok; 3 el stop no esta activo (nada que
        levantar); 4 la base no existe.

    Efectos:
        Lee `system_state` en solo lectura. Con `confirm` y el stop activo abre la base con
        `Database.connect()`, lee `trades` y la puerta de resultados, y escribe `system_state`
        (`risk_engine.resume_drawdown_stop`: `drawdown_stop_active`,
        `drawdown_breach_in_progress`, `equity_peak_usdt` y `drawdown_stop_last_resumed_at`).
    """
    path = Path(db_path)
    if not path.is_file():
        return EXIT_NO_DB, f"No existe la base: {path}"
    active, last_resumed = read_stop_state(path)
    state = "ACTIVO" if active else "inactivo"
    lines = [f"Stop por drawdown: {state}"
             + (f" (ultimo levantamiento manual: {last_resumed})" if last_resumed else "")]
    if not confirm:
        lines.append(
            "Sin cambios (modo informativo). Para levantarlo: repite con --confirm. Reinicia el "
            "pico de equity al equity actual y NO reinicia el contador del criterio de paso a "
            "dinero real."
            if active else "No hay nada que levantar."
        )
        return EXIT_OK, "\n".join(lines)
    if not active:
        lines.append("No hay nada que levantar: no se hizo ningun cambio.")
        return EXIT_NOT_ACTIVE, "\n".join(lines)

    db = Database(str(path))
    await db.connect()
    try:
        capital = settings.initial_capital_usdt if initial_capital is None else initial_capital
        used = equity if equity is not None else await realized_equity(db, capital)
        await risk_engine.resume_drawdown_stop(db, used)
        shown = (
            f"pico de equity reiniciado a {used:.4f} USDT" if await reveal_allowed(db)
            else "pico de equity reiniciado al equity actual (valor oculto: los resultados "
                 "siguen ocultos hasta el registro oficial del veredicto)"
        )
        count = await system_state_repo.get_state(db, "drawdown_stop_would_have_triggered_count")
    finally:
        await db.close()
    lines += [
        f"Stop por drawdown LEVANTADO; {shown}.",
        f"El contador del criterio de paso a dinero real NO se toco (valor {count or 0}).",
        "Las entradas nuevas vuelven a evaluarse; el bot no necesita reiniciarse.",
    ]
    return EXIT_OK, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada: valida las opciones, ejecuta `run` e imprime el mensaje.

    Sin `--db` y con el bot en Docker se niega (codigo 2): la base por defecto es una copia
    vieja y levantar el stop ahi no cambiaria nada en el bot.

    Args:
        argv: argumentos (por defecto, los de la linea de comandos).

    Returns:
        El codigo de salida: 0 ok; 2 error de uso; 3 el stop no esta activo; 4 la base no
        existe.

    Efectos:
        Los de `run`. El mensaje va a stdout (a stderr con los codigos 2 y 4).
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=None, help=f"base del bot (def.: {DEFAULT_DB})")
    parser.add_argument("--confirm", action="store_true", help="levantar el stop (si no, mira)")
    parser.add_argument(
        "--equity", type=float, default=None,
        help="equity a usar como nuevo pico (def.: capital inicial + PnL realizado)",
    )
    args = parser.parse_args(argv)
    if args.equity is not None and args.equity <= 0:
        parser.error("--equity debe ser mayor que 0")
    if args.db is None:
        # Tras la mudanza a Docker la base por defecto es una copia VIEJA: levantar el stop ahi no
        # cambiaria nada en el bot y daria por arreglado algo que no lo esta.
        refusal = docker_mode_refusal(
            DEFAULT_DB,
            "Levantalo en el contenedor: docker compose exec minerva python "
            "scripts/resume_drawdown_stop.py --confirm.",
        )
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT_USAGE
    code, message = asyncio.run(run(args.db or DEFAULT_DB, args.confirm, args.equity))
    print(message, file=sys.stderr if code in (EXIT_USAGE, EXIT_NO_DB) else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
