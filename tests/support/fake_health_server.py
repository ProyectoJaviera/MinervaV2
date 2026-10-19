"""Un servidor de `/health` falso para las pruebas de la mudanza (Fase 7).

    python fake_health_server.py <puerto> <archivo_json>

Responde `GET /health` con el JSON del archivo (se vuelve a leer en cada peticion, asi una prueba
puede cambiar el estado "en caliente") y 404 a todo lo demas. Hace de bot nativo, de contenedor o
de lo que la prueba necesite; solo escucha en 127.0.0.1.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def make_handler(payload_file: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - nombre fijado por http.server
            if self.path.split("?")[0] != "/health":
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = json.dumps(json.loads(payload_file.read_text(encoding="utf-8"))).encode()
            except (OSError, ValueError):
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # silencio
            return

    return Handler


def main() -> None:
    port, payload_file = int(sys.argv[1]), Path(sys.argv[2])
    HTTPServer(("127.0.0.1", port), make_handler(payload_file)).serve_forever()


if __name__ == "__main__":
    main()
