"""Genera el hash bcrypt de tu contrasena para `AUTH_PASSWORD_HASH` en `.env`
(pre-Fase 5, item 2 del plan aprobado 2026-10-14). De uso UNICO: pide la
contrasena con `getpass` (nunca como argumento de linea de comandos -- eso
quedaria en el historial de la shell) e imprime SOLO el hash resultante.
Nunca toca `.env`: lo pegas tu mismo.

El valor se imprime ENTRE COMILLAS SIMPLES. Un hash bcrypt lleva signos `$` (`$2b$12$...`) y
Docker Compose, que lee el `.env` de la raiz en cada comando (`docker compose up/ps/logs...`),
interpreta los `$` de un valor sin comillas simples como variables: muestra en pantalla trozos del
hash ("The "XYZ" variable is not set") y lo recorta. Entre comillas simples nadie lo toca, y la
aplicacion (python-dotenv) lo lee igual con o sin ellas. Fase 7, docs/MUDANZA_DOCKER.md.

    python scripts/hash_password.py
"""

from __future__ import annotations

import getpass
import sys

sys.path.insert(0, ".")

from app.api.security import hash_password  # noqa: E402

BCRYPT_MAX_PASSWORD_BYTES = 72


def build_hash_line(password: str, confirm: str) -> str:
    """La linea `AUTH_PASSWORD_HASH='...'` lista para pegar en `.env`.

    El hash va entre comillas simples porque lleva signos `$` que Docker Compose interpretaria
    como variables.

    Args:
        password: la contrasena.
        confirm: la contrasena repetida.

    Returns:
        La linea con el hash bcrypt.

    Raises:
        ValueError: si la contrasena esta vacia, no coincide con la confirmacion, o ocupa mas
            de 72 bytes en UTF-8 (limite de bcrypt).
    """
    if not password:
        raise ValueError("La contrasena no puede estar vacia.")
    if password != confirm:
        raise ValueError("No coinciden.")
    n_bytes = len(password.encode("utf-8"))
    if n_bytes > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"La contrasena ocupa {n_bytes} bytes en UTF-8 y bcrypt solo acepta hasta "
            f"{BCRYPT_MAX_PASSWORD_BYTES} (los caracteres no ASCII ocupan 2-4 bytes cada uno). "
            "Usa una mas corta."
        )
    return "AUTH_PASSWORD_HASH='" + hash_password(password) + "'"


if __name__ == "__main__":
    password = getpass.getpass("Contrasena para AUTH_PASSWORD_HASH: ")
    confirm = getpass.getpass("Repetila: ") if password else ""
    try:
        line = build_hash_line(password, confirm)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print("\n" + line)
