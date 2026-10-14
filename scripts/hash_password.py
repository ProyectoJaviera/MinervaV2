"""Genera el hash bcrypt de tu contrasena para `AUTH_PASSWORD_HASH` en `.env`
(pre-Fase 5, item 2 del plan aprobado 2026-10-14). De uso UNICO: pide la
contrasena con `getpass` (nunca como argumento de linea de comandos -- eso
quedaria en el historial de la shell) e imprime SOLO el hash resultante.
Nunca toca `.env`: lo pegas tu mismo.

    python scripts/hash_password.py
"""

from __future__ import annotations

import getpass
import sys

sys.path.insert(0, ".")

from app.api.security import hash_password  # noqa: E402

if __name__ == "__main__":
    password = getpass.getpass("Contrasena para AUTH_PASSWORD_HASH: ")
    if not password:
        raise SystemExit("La contrasena no puede estar vacia.")
    confirm = getpass.getpass("Repetila: ")
    if password != confirm:
        raise SystemExit("No coinciden.")
    print("\nAUTH_PASSWORD_HASH=" + hash_password(password))
