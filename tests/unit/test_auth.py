"""Autenticacion de un solo usuario local (pre-Fase 5, item 2 del plan
aprobado 2026-10-14): `app/api/security.py` + `app/api/routes/auth.py`.
Primer uso de `fastapi.testclient.TestClient` en el repo -- una app FastAPI
minima (solo el router de auth + `app.state.settings`), sin tocar la app
real ni ningun puerto/DB."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.security import (
    COOKIE_NAME,
    JWT_ALGORITHM,
    MIN_SECRET_LENGTH,
    create_session_token,
    decode_session_token,
    hash_password,
    jwt_secret_is_safe,
    verify_password,
    verify_username,
    warn_if_jwt_secret_is_unsafe,
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


# --- correccion 1: JWT_SECRET vacio o corto falla cerrado, nunca 500 -----------

# Secreto >= 32 caracteres pero con FORMA de JWK: pasa el chequeo de largo de
# jwt_secret_is_safe (que solo mira len()) pero sigue haciendo que PyJWT
# lance InvalidKeyError (no InvalidTokenError) al intentar usarlo de verdad --
# el caso real que exige capturar jwt.PyJWTError en vez de solo
# jwt.InvalidTokenError, no solo el chequeo de largo.
JWK_SHAPED_LONG_SECRET = '{"kty": "oct", "k": "abcdefghijklmnopqrstuvwxyz1234567890"}'


@pytest.mark.parametrize("secret,expected", [
    ("", False), ("x" * 31, False), ("x" * 32, True), ("x" * 50, True),
])
def test_jwt_secret_is_safe_boundary(secret, expected):
    assert jwt_secret_is_safe(secret) is expected


def test_warn_if_jwt_secret_is_unsafe_logs_a_warning(caplog):
    settings = make_settings(JWT_SECRET="corto")
    logger = logging.getLogger("test.jwt.warning")

    with caplog.at_level(logging.WARNING, logger="test.jwt.warning"):
        warn_if_jwt_secret_is_unsafe(logger, settings)

    assert any("JWT_SECRET" in r.getMessage() for r in caplog.records)


def test_warn_if_jwt_secret_is_safe_logs_nothing(caplog):
    settings = make_settings(JWT_SECRET="x" * MIN_SECRET_LENGTH)
    logger = logging.getLogger("test.jwt.warning2")

    with caplog.at_level(logging.WARNING, logger="test.jwt.warning2"):
        warn_if_jwt_secret_is_unsafe(logger, settings)

    assert caplog.records == []


def test_login_returns_503_when_jwt_secret_is_too_short():
    client = make_client(make_settings(JWT_SECRET="corto"))

    response = _login(client)

    assert response.status_code == 503
    assert COOKIE_NAME not in response.cookies


def test_login_returns_503_when_jwt_secret_is_empty():
    client = make_client(make_settings(JWT_SECRET=""))

    response = _login(client)

    assert response.status_code == 503


def test_me_returns_401_not_500_when_jwt_secret_is_too_short():
    """Antes de la correccion, un JWT_SECRET vacio/corto hacia que
    decode_session_token lanzara jwt.InvalidKeyError (ver mas abajo), que
    escapaba sin atrapar y volvia un 500 -- aunque nunca daba acceso."""
    client = make_client(make_settings(JWT_SECRET="corto"))
    client.cookies.set(COOKIE_NAME, "cualquier-cosa")

    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_decode_session_token_with_an_empty_secret_raises_a_pyjwt_error():
    """Reproduce el bug real: decodificar un token BIEN FORMADO contra un
    secreto vacio lanza jwt.InvalidKeyError -- subclase directa de
    jwt.PyJWTError, NO de jwt.InvalidTokenError. Antes de la correccion,
    `except jwt.InvalidTokenError` en require_auth no la atrapaba."""
    token = create_session_token("renzo", secret=TEST_SECRET)

    with pytest.raises(jwt.PyJWTError) as exc_info:
        decode_session_token(token, secret="")

    assert not isinstance(exc_info.value, jwt.InvalidTokenError)
    assert isinstance(exc_info.value, jwt.InvalidKeyError)


def test_me_returns_401_not_500_with_a_jwk_shaped_secret_that_passes_the_length_check():
    """El secreto es >= 32 caracteres (pasa jwt_secret_is_safe), pero tiene
    FORMA de JWK -- PyJWT lanza InvalidKeyError igual al intentar decodificar.
    Esto es lo que de verdad ejercita `except jwt.PyJWTError` en require_auth,
    no solo el chequeo de largo (que aqui no alcanza a bloquearlo)."""
    assert jwt_secret_is_safe(JWK_SHAPED_LONG_SECRET)  # pasa el chequeo de largo
    client = make_client(make_settings(JWT_SECRET=JWK_SHAPED_LONG_SECRET))
    client.cookies.set(COOKIE_NAME, "cualquier-cosa")

    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_login_returns_503_not_500_with_a_jwk_shaped_secret_that_passes_the_length_check():
    assert jwt_secret_is_safe(JWK_SHAPED_LONG_SECRET)
    client = make_client(make_settings(JWT_SECRET=JWK_SHAPED_LONG_SECRET))

    response = _login(client)

    assert response.status_code == 503


# --- correccion 2: verify_username con caracteres no ASCII ---------------------


def test_verify_username_accepts_non_ascii_characters_without_raising():
    assert verify_username("usuário", "usuário") is True
    assert verify_username("usuário", "renzo") is False


def test_login_with_a_non_ascii_username_returns_401_not_500():
    client = make_client()

    response = _login(client, username="usuário")

    assert response.status_code == 401


# --- correccion 3: verify_password con contrasenas > 72 bytes ------------------


def test_verify_password_rejects_a_password_longer_than_72_bytes_without_raising():
    hashed = hash_password("corta")
    too_long = "a" * 100

    assert verify_password(too_long, hashed) is False


def test_login_with_a_password_longer_than_72_bytes_returns_401_not_500():
    client = make_client()

    response = _login(client, password="a" * 100)

    assert response.status_code == 401


def test_login_request_rejects_a_password_over_128_characters_at_the_validation_layer():
    client = make_client()

    response = client.post(
        "/api/auth/login", json={"username": "renzo", "password": "a" * 129}
    )

    assert response.status_code == 422  # rechazado por pydantic, antes de verify_password
