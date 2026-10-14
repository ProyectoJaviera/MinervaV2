"""`scripts/hash_password.py`: validaciones antes de hashear (bcrypt acepta
como maximo 72 bytes -- en UTF-8, no 72 caracteres)."""

from __future__ import annotations

import pytest

from app.api.security import verify_password
from scripts.hash_password import build_hash_line


def test_valid_password_produces_an_env_line_that_verifies():
    line = build_hash_line("mi-contrasena", "mi-contrasena")

    assert line.startswith("AUTH_PASSWORD_HASH=")
    assert verify_password("mi-contrasena", line.removeprefix("AUTH_PASSWORD_HASH="))


def test_password_of_exactly_72_bytes_is_accepted():
    password = "a" * 72

    assert build_hash_line(password, password).startswith("AUTH_PASSWORD_HASH=")


def test_password_of_73_ascii_bytes_is_rejected_with_a_clear_message():
    password = "a" * 73

    with pytest.raises(ValueError, match=r"73 bytes.*72"):
        build_hash_line(password, password)


def test_password_short_in_characters_but_over_72_bytes_in_utf8_is_rejected():
    password = "ñ" * 40  # 40 caracteres, 80 bytes en UTF-8

    assert len(password) == 40
    with pytest.raises(ValueError, match=r"80 bytes"):
        build_hash_line(password, password)


def test_empty_password_is_rejected():
    with pytest.raises(ValueError, match="vacia"):
        build_hash_line("", "")


def test_mismatched_confirmation_is_rejected():
    with pytest.raises(ValueError, match="No coinciden"):
        build_hash_line("una-clave", "otra-clave")
