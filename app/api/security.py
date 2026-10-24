"""Autenticacion de un solo usuario local (pre-Fase 5, item 2 del plan
aprobado 2026-10-14) -- JWT HS256 (`PyJWT`) en una cookie `httpOnly`. Ningun
endpoint de este proyecto abre ni cierra posiciones via HTTP
(`AUTO_OPEN_WITHOUT_LLM` sigue en `false`); esto gatea LECTURA de datos mas
detallados que `GET /health` (que queda deliberadamente sin auth, para que
`scripts/telegram_alert.py` siga pudiendolo consultar sin login). Unica
excepcion, desde la Fase 5: las tres acciones de CONTROL del dashboard
(pausar, reanudar, kill switch, `app/api/routes/control.py`), que exigen
este login, mismo origen, JSON y una palabra de confirmacion, y quedan
auditadas; no tocan posiciones.

Hash de contraseña con `bcrypt` directo (no `passlib`). Token con `sub`
(username), `iat`, `exp` = `iat` + `SESSION_DURATION` (24h, un solo usuario
local -- sin refresh token, innecesario para este caso). Transporte: cookie
`httpOnly`, `SameSite=Strict`, `secure=False` (todo corre en loopback HTTP
plano; `Secure` rompería la cookie en silencio).

**Revision 2026-10-14 (3 correcciones de seguridad del usuario, probadas
contra la Subfase B real: ninguna daba acceso, pero fallaban con 500 en vez
de un 401/503 limpio):** un `JWT_SECRET` vacio o corto deja todo el sistema
fallando ABIERTO en la practica -- un atacante que lo adivine (vacio es
adivinable al primer intento) puede forjar un token valido para cualquier
usuario, porque `jwt.decode`/`jwt.encode` no rechazan una clave HMAC corta
por si solos, solo advierten. `jwt_secret_is_safe` corta ese camino ANTES de
firmar o verificar nada."""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from app.api.deps import get_settings
from app.config import Settings

COOKIE_NAME = "minerva_session"
JWT_ALGORITHM = "HS256"
SESSION_DURATION = timedelta(hours=24)
# RFC 7518 Section 3.2 recomienda al menos 256 bits (32 bytes) de clave HMAC
# para HS256 -- PyJWT solo lo advierte (InsecureKeyLengthWarning), no lo
# rechaza, asi que se exige aqui explicitamente. El valor real se genera con
# `python -c "import secrets; print(secrets.token_hex(32))"` (ver el plan
# aprobado / docstring de scripts/run_dev_server.py), que da 64 caracteres.
MIN_SECRET_LENGTH = 32
# bcrypt trunca/rechaza mas alla de 72 bytes (limite propio del algoritmo);
# 128 caracteres es un margen generoso sobre eso para el body del request,
# sin aceptar un payload de tamano arbitrario.
MAX_CREDENTIAL_LENGTH = 128


def jwt_secret_is_safe(secret: str) -> bool:
    """Dice si `JWT_SECRET` cumple el minimo de 32 caracteres (256 bits para HS256).

    Con un secreto vacio o corto un atacante podria forjar un token valido: por eso se
    corta el camino ANTES de firmar o verificar nada.

    Args:
        secret: el secreto configurado.

    Returns:
        True si es suficientemente largo.
    """
    return len(secret) >= MIN_SECRET_LENGTH


def warn_if_jwt_secret_is_unsafe(logger: logging.Logger, settings: Settings) -> None:
    """Llamado una vez al arrancar el proceso (`app/main.py`). No impide
    arrancar (`/health`/`/positions`/`/trades` no necesitan auth) -- solo
    deja constancia de que las rutas protegidas bajo `/api/` van a rechazar
    todo (`login` con 503, `require_auth` con 401) hasta que se corrija."""
    if not jwt_secret_is_safe(settings.jwt_secret):
        logger.warning(
            "JWT_SECRET no esta configurado o es muy corto (se exigen al menos 32 "
            "caracteres): las rutas protegidas bajo /api/ van a rechazar todo hasta "
            "que se corrija en .env."
        )


def hash_password(password: str) -> str:
    """Hash bcrypt de una contrasena (para `AUTH_PASSWORD_HASH`).

    Args:
        password: la contrasena en claro.

    Returns:
        El hash bcrypt como texto.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """`False` si `password_hash` esta vacio (usuario nunca configurado) --
    nunca se intenta `bcrypt.checkpw` contra un hash vacio -- o si `password`
    supera los 72 bytes que bcrypt acepta (`ValueError` en vez de una
    comparacion): una contrasena invalida por tamano no es una contrasena
    correcta, nunca deberia tumbar el endpoint con un 500."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def verify_username(username: str, expected_username: str) -> bool:
    """Compara BYTES (UTF-8), no `str`: `hmac.compare_digest` exige que un
    `str` sea puro ASCII y lanza `TypeError` ante cualquier caracter que no
    lo sea -- un usuario con tilde o similar rompia esto con un 500 antes de
    esta correccion. Comparar bytes no tiene esa restriccion."""
    if not expected_username:
        return False
    return hmac.compare_digest(username.encode("utf-8"), expected_username.encode("utf-8"))


def create_session_token(username: str, *, secret: str, now: datetime | None = None) -> str:
    """Crea el JWT de sesion (HS256) con `sub`, `iat` y `exp` (24 horas despues).

    Args:
        username: usuario autenticado.
        secret: `JWT_SECRET`.
        now: instante de emision; por defecto, ahora (UTC).

    Returns:
        El token firmado.

    Raises:
        jwt.PyJWTError: si el secreto tiene una forma que la libreria rechaza.
    """
    now = now or datetime.now(UTC)
    payload = {"sub": username, "iat": now, "exp": now + SESSION_DURATION}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str, *, secret: str) -> str:
    """Devuelve el `sub` (username) o lanza `jwt.PyJWTError` -- la clase BASE
    de PyJWT, no `InvalidTokenError`: `InvalidKeyError` (clave HMAC
    malformada) es subclase directa de `PyJWTError`, NO de
    `InvalidTokenError`, y antes de esta correccion escapaba sin atrapar."""
    payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    return payload["sub"]


async def require_auth(request: Request, settings: Settings = Depends(get_settings)) -> str:
    """Dependencia para proteger un router ENTERO
    (`APIRouter(dependencies=[Depends(require_auth)])`), no endpoint por
    endpoint -- asi una ruta nueva que alguien agregue despues a ese router
    queda protegida por construccion.

    Si `JWT_SECRET` es inseguro (vacio o corto), SIEMPRE devuelve 401 sin
    intentar decodificar nada -- nunca se confia en un token firmado (o
    forjable) con una clave que no cumple el minimo, y nunca se deja escapar
    una excepcion de PyJWT como un 500."""
    if not jwt_secret_is_safe(settings.jwt_secret):
        raise HTTPException(status_code=401, detail="No autenticado")
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        return decode_session_token(token, secret=settings.jwt_secret)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada") from None
