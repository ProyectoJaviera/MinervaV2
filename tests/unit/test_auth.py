"""Autenticacion de un solo usuario local (pre-Fase 5, item 2 del plan
aprobado 2026-10-14): `app/api/security.py` + `app/api/routes/auth.py`.
Primer uso de `fastapi.testclient.TestClient` en el repo -- una app FastAPI
minima (solo el router de auth + `app.state.settings`), sin tocar la app
real ni ningun puerto/DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.security import (
    COOKIE_NAME,
    JWT_ALGORITHM,
    create_session_token,
    decode_session_token,
    hash_password,
    verify_password,
    verify_username,
)
from app.config import Settings

VALID_PASSWORD = "correcta-123"
# >= 32 bytes a proposito: PyJWT advierte (InsecureKeyLengthWarning) por debajo
# de eso para HS256 -- el real se genera con secrets.token_hex(32) (ver
# scripts/run_dev_server.py / el plan aprobado), asi que los de prueba imitan
# ese largo en vez de uno corto que dispare la advertencia en cada test.
TEST_SECRET = "s3cr3t-de-prueba-de-al-menos-32-bytes-de-largo"


def make_settings(**overrides) -> Settings:
    defaults = {
        "AUTH_USERNAME": "renzo", "AUTH_PASSWORD_HASH": hash_password(VALID_PASSWORD),
        "JWT_SECRET": "un-secreto-de-prueba-bien-largo-no-el-real",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_client(settings: Settings | None = None) -> TestClient:
    app = FastAPI()
    app.state.settings = settings or make_settings()
    app.include_router(auth_routes.router)
    return TestClient(app)


def _login(client: TestClient, username: str = "renzo", password: str = VALID_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# --- funciones puras: hash/verify de contrasena y usuario -----------------------


def test_hash_password_round_trips_through_verify_password():
    hashed = hash_password("mi-contrasena")
    assert verify_password("mi-contrasena", hashed) is True


def test_verify_password_rejects_the_wrong_password():
    hashed = hash_password("mi-contrasena")
    assert verify_password("otra-cosa", hashed) is False


def test_verify_password_rejects_an_empty_hash():
    # Nunca se llama a bcrypt.checkpw contra un hash vacio (usuario nunca
    # configurado) -- bcrypt lanzaria, no solo devolveria False.
    assert verify_password("cualquier-cosa", "") is False


def test_verify_username_rejects_an_empty_expected_username():
    assert verify_username("renzo", "") is False


def test_verify_username_accepts_the_matching_username():
    assert verify_username("renzo", "renzo") is True


def test_verify_username_rejects_a_different_username():
    assert verify_username("otro", "renzo") is False


# --- funciones puras: token JWT --------------------------------------------------


def test_create_and_decode_session_token_round_trip():
    token = create_session_token("renzo", secret=TEST_SECRET)
    assert decode_session_token(token, secret=TEST_SECRET) == "renzo"


def test_decode_session_token_rejects_a_token_signed_with_a_different_secret():
    token = create_session_token("renzo", secret=TEST_SECRET)
    with pytest.raises(jwt.InvalidTokenError):
        decode_session_token(token, secret=TEST_SECRET[::-1])


def test_decode_session_token_rejects_an_expired_token():
    old_now = datetime.now(UTC) - timedelta(hours=100)
    token = create_session_token("renzo", secret=TEST_SECRET, now=old_now)
    with pytest.raises(jwt.InvalidTokenError):
        decode_session_token(token, secret=TEST_SECRET)


def test_decode_session_token_rejects_garbage():
    with pytest.raises(jwt.InvalidTokenError):
        decode_session_token("esto-no-es-un-jwt", secret=TEST_SECRET)


# --- POST /api/auth/login --------------------------------------------------------


def test_login_with_correct_credentials_sets_a_cookie_and_returns_ok():
    client = make_client()

    response = _login(client)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert COOKIE_NAME in response.cookies


def test_login_with_the_wrong_password_returns_401_and_sets_no_cookie():
    client = make_client()

    response = _login(client, password="mala")

    assert response.status_code == 401
    assert COOKIE_NAME not in response.cookies


def test_login_with_the_wrong_username_returns_401():
    client = make_client()

    response = _login(client, username="otro")

    assert response.status_code == 401


def test_login_when_auth_was_never_configured_always_fails():
    settings = make_settings(AUTH_USERNAME="", AUTH_PASSWORD_HASH="")
    client = make_client(settings)

    response = _login(client, username="", password="")

    assert response.status_code == 401


def test_the_session_cookie_is_httponly_and_samesite_strict():
    client = make_client()

    response = _login(client)

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "secure" not in set_cookie  # loopback HTTP plano, nunca Secure


# --- GET /api/auth/me (protegido por require_auth) ------------------------------


def test_me_without_a_cookie_returns_401():
    client = make_client()

    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_me_with_a_valid_session_returns_the_username():
    client = make_client()
    _login(client)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {"username": "renzo"}


def test_me_with_a_tampered_cookie_returns_401():
    client = make_client()
    client.cookies.set(COOKIE_NAME, "esto-no-es-un-token-valido")

    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_me_with_a_token_signed_with_the_wrong_secret_returns_401():
    client = make_client()
    foreign_token = jwt.encode(
        {"sub": "renzo", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)},
        TEST_SECRET[::-1], algorithm=JWT_ALGORITHM,
    )
    client.cookies.set(COOKIE_NAME, foreign_token)

    response = client.get("/api/auth/me")

    assert response.status_code == 401


# --- POST /api/auth/logout -------------------------------------------------------


def test_logout_clears_the_session_so_me_fails_afterward():
    client = make_client()
    _login(client)
    assert client.get("/api/auth/me").status_code == 200

    logout_response = client.post("/api/auth/logout")

    assert logout_response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
