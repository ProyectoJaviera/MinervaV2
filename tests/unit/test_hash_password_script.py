"""`scripts/hash_password.py`: validaciones antes de hashear (bcrypt acepta como maximo 72
bytes -- en UTF-8, no 72 caracteres) y el formato de la linea que se pega en `.env`."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.security import verify_password
from app.config import Settings
from scripts.hash_password import build_hash_line

PREFIX = "AUTH_PASSWORD_HASH="


def hash_of(line: str) -> str:
    """El hash de una linea `AUTH_PASSWORD_HASH='...'` (sin las comillas)."""
    value = line.removeprefix(PREFIX)
    assert value[0] == value[-1] == "'", "el hash debe ir entre comillas simples"
    return value[1:-1]


def test_valid_password_produces_an_env_line_that_verifies():
    line = build_hash_line("mi-contrasena", "mi-contrasena")

    assert line.startswith(PREFIX)
    assert verify_password("mi-contrasena", hash_of(line))


def test_the_hash_goes_between_single_quotes_because_compose_interpolates_the_dollar_signs():
    # Fase 7: sin comillas simples, `docker compose ...` lee el `.env` de la raiz, toma
    # `$IWD...` por una variable y escribe trozos del hash en pantalla. Entre comillas simples
    # Compose lo deja intacto.
    line = build_hash_line("mi-contrasena", "mi-contrasena")

    value = line.removeprefix(PREFIX)
    assert value.startswith("'$2") and value.endswith("'")
    assert "'" not in value[1:-1] and '"' not in value
    assert value.count("$") == 3  # $2b$12$<sal+hash>: nada mas lleva '$'


def test_the_application_reads_the_quoted_line_from_a_real_env_file(tmp_path: Path):
    # La linea se pega en `.env` tal cual: `Settings` (python-dotenv) devuelve el hash SIN comillas.
    line = build_hash_line("mi-contrasena", "mi-contrasena")
    env_file = tmp_path / "prueba.env"
    env_file.write_text(line + "\n", encoding="utf-8")

    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]

    assert settings.auth_password_hash == hash_of(line)
    assert verify_password("mi-contrasena", settings.auth_password_hash)


def test_the_application_also_reads_the_old_unquoted_form(tmp_path: Path):
    # Los `.env` que ya existen traen el hash SIN comillas: siguen funcionando.
    hash_value = hash_of(build_hash_line("mi-contrasena", "mi-contrasena"))
    env_file = tmp_path / "antiguo.env"
    env_file.write_text(PREFIX + hash_value + "\n", encoding="utf-8")

    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]

    assert settings.auth_password_hash == hash_value


def test_password_of_exactly_72_bytes_is_accepted():
    password = "a" * 72

    assert build_hash_line(password, password).startswith(PREFIX)


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
