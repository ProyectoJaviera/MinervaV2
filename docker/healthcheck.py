"""Healthcheck del contenedor (Fase 6, docs/FASE6_PLAN.md): consulta `GET /health` del proceso.

Solo biblioteca estandar y sin importar nada de `app/`: arranca al instante, no depende del
entorno de la aplicacion y la imagen no necesita `curl`. Codigo de salida 0 = sano, 1 = no sano
(Docker no usa otros). Imprime UNA linea con el motivo; Docker la guarda en
`docker inspect --format '{{json .State.Health}}'`.

Que cuenta como sano:
- HTTP 200 con `status: "ok"`, o
- `status: "stale"` PERO con `control.paused` verdadero: una pausa deliberada (Telegram `/pause`,
  `/kill` o el dashboard) detiene el scheduler y `/health` pasa a `stale` a los 90 min a
  proposito; no es una falla.
Y ademas `llm_labeling.frozen_hash_ok` no puede ser falso: con el hash del prompt desalineado
el etiquetado en vivo se detiene solo y la medicion no avanza aunque el proceso responda. Es
la falla silenciosa mas cara de este proyecto, asi que se marca `unhealthy` en vez de dejarla
pasar.

`MINERVA_HEALTH_URL` cambia la direccion (solo para pruebas). Sin respuesta, con HTTP distinto
de 200 o con un cuerpo que no es el esperado, el contenedor queda no sano.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000/health"
TIMEOUT_SECONDS = 5.0
MAX_BODY_BYTES = 1_000_000


def evaluate(body: object) -> tuple[bool, str]:
    """Decide si el cuerpo de `/health` es sano. Pura: no hace red."""
    if not isinstance(body, dict):
        return False, "cuerpo inesperado de /health (no es un objeto JSON)"
    labeling = body.get("llm_labeling")
    if isinstance(labeling, dict) and labeling.get("frozen_hash_ok") is False:
        return False, "frozen_hash_ok=false: el hash del prompt no coincide, etiquetado detenido"
    status = body.get("status")
    if status == "ok":
        return True, "ok"
    if status == "stale":
        control = body.get("control")
        if isinstance(control, dict) and control.get("paused") is True:
            return True, "scheduler en pausa deliberada (stale esperado)"
        return False, "stale: el ciclo del scheduler no avanza (mas de 90 min sin ciclo)"
    return False, f"estado desconocido en /health: {status!r}"


def check(url: str = DEFAULT_URL, timeout: float = TIMEOUT_SECONDS) -> tuple[bool, str]:
    # Sin proxies a proposito: tras un proxy corporativo el contenedor tiene HTTP_PROXY definido
    # (lo necesita para salir a internet) y urllib mandaria tambien esta consulta, que es a
    # 127.0.0.1, al proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            raw = response.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de red es "sin respuesta"
        return False, f"sin respuesta de {url}: {type(exc).__name__}"
    try:
        body = json.loads(raw)
    except ValueError:
        return False, "respuesta de /health que no es JSON"
    return evaluate(body)


def main() -> int:
    healthy, reason = check(os.environ.get("MINERVA_HEALTH_URL", DEFAULT_URL))
    print(reason)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
