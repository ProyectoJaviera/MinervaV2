"""Consulta `GET /health` con timeout corto y reporta OK/STALE/SIN_RESPUESTA
(incidente de estabilidad 2026-10-11) -- pensado para un cron/Task Scheduler
EXTERNO al propio bot, que no puede avisar si el bot entero se congelo.

    python scripts/check_health.py --port 8000
"""

from __future__ import annotations

import argparse
import sys

import httpx

OK = 0
STALE = 1
SIN_RESPUESTA = 2


def fetch_health(host: str, port: int, timeout: float) -> tuple[dict | None, str | None]:
    """(JSON de /health, None) o (None, "<url>: <error>") si no hubo respuesta."""
    url = f"http://{host}:{port}/health"
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:  # noqa: BLE001 - cualquier falla de red/HTTP es "sin respuesta"
        return None, f"{url}: {exc}"


def main(host: str, port: int, timeout: float) -> int:
    data, error = fetch_health(host, port, timeout)
    if data is None:
        print(f"SIN_RESPUESTA: {error}")
        return SIN_RESPUESTA

    if data.get("status") == "ok":
        print(f"OK: {data}")
        return OK
    print(f"STALE: {data}")
    return STALE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    sys.exit(main(args.host, args.port, args.timeout))
