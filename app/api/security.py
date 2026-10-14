"""Autenticacion de un solo usuario local (pre-Fase 5, item 2 del plan
aprobado 2026-10-14) -- JWT HS256 (`PyJWT`) en una cookie `httpOnly`. Nunca
protege una escritura: ningun endpoint de este proyecto abre, cierra ni
controla el bot via HTTP (`AUTO_OPEN_WITHOUT_LLM` sigue en `false`); esto
solo gatea LECTURA de datos mas detallados que `GET /health` (que queda
deliberadamente sin auth, para que `scripts/telegram_alert.py` siga
pudiendolo consultar sin login).

Hash de contraseña con `bcrypt` directo (no `passlib`). Token con `sub`
(username), `iat`, `exp` = `iat` + `SESSION_DURATION` (24h, un solo usuario
local -- sin refresh token, innecesario para este caso). Transporte: cookie
`httpOnly`, `SameSite=Strict`, `secure=False` (todo corre en loopback HTTP
plano; `Secure` rompería la cookie en silencio)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from app.api.deps import get_settings
from app.config import Settings

COOKIE_NAME = "minerva_session"
JWT_ALGORITHM = "HS256"
SESSION_DURATION = timedelta(hours=24)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """`False` si `password_hash` esta vacio (usuario nunca configurado) --
    nunca se intenta `bcrypt.checkpw` contra un hash vacio."""
    if not password_hash:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def verify_username(username: str, expected_username: str) -> bool:
    """`hmac.compare_digest` en vez de `==`: mismo tratamiento prolijo que
    `verify_password` (via `bcrypt.checkpw`), aunque el riesgo real de
    timing es bajisimo detras de loopback con un solo usuario local."""
    if not expected_username:
        return False
    return hmac.compare_digest(username, expected_username)


def create_session_token(username: str, *, secret: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    payload = {"sub": username, "iat": now, "exp": now + SESSION_DURATION}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str, *, secret: str) -> str:
    """Devuelve el `sub` (username) o lanza `jwt.InvalidTokenError` (firma
    invalida, expirado, formato roto -- todas subclases de esa excepcion)."""
    payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    return payload["sub"]


async def require_auth(request: Request, settings: Settings = Depends(get_settings)) -> str:
    """Dependencia para proteger un router ENTERO
    (`APIRouter(dependencies=[Depends(require_auth)])`), no endpoint por
    endpoint -- asi una ruta nueva que alguien agregue despues a ese router
    queda protegida por construccion."""
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        return decode_session_token(token, secret=settings.jwt_secret)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada") from None
