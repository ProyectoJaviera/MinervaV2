"""Login/logout de un solo usuario local (pre-Fase 5, item 2 del plan
aprobado 2026-10-14). Solo abre o cierra una sesion -- no controla el bot
(`AUTO_OPEN_WITHOUT_LLM` sigue en `false`, nada de esto es un endpoint de
escritura sobre el trading). Los controles del dashboard (Fase 5) viven en
`app/api/routes/control.py`."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import get_settings
from app.api.security import (
    COOKIE_NAME,
    MAX_CREDENTIAL_LENGTH,
    SESSION_DURATION,
    create_session_token,
    jwt_secret_is_safe,
    require_auth,
    verify_password,
    verify_username,
)
from app.config import Settings

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(max_length=MAX_CREDENTIAL_LENGTH)
    password: str = Field(max_length=MAX_CREDENTIAL_LENGTH)


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    if not jwt_secret_is_safe(settings.jwt_secret):
        # JWT_SECRET vacio o corto: nunca se firma un token con una clave
        # insegura -- un atacante que la adivine (vacio es adivinable al
        # primer intento) podria forjar una sesion valida para cualquier
        # usuario. Se avisa claro en vez de firmar igual o tumbar con un 500.
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET no esta configurado o es muy corto (se exigen al menos "
            "32 caracteres) -- revisa tu .env antes de iniciar sesion",
        )

    valid_username = verify_username(body.username, settings.auth_username)
    valid_password = verify_password(body.password, settings.auth_password_hash)
    if not (valid_username and valid_password):
        # Mensaje generico a proposito: no distingue "usuario no existe" de
        # "contrasena incorrecta".
        raise HTTPException(status_code=401, detail="Usuario o contrasena incorrectos")

    try:
        token = create_session_token(body.username, secret=settings.jwt_secret)
    except jwt.PyJWTError:
        # `jwt_secret_is_safe` solo mira el largo -- un secreto largo pero
        # con forma de PEM/JWK sigue haciendo que `jwt.encode` lance
        # (`InvalidKeyError`, subclase DIRECTA de `PyJWTError`, no de
        # `InvalidTokenError`). Mismo principio que `require_auth`: nunca
        # dejar escapar esto como un 500.
        raise HTTPException(
            status_code=503, detail="JWT_SECRET invalido -- revisa tu .env",
        ) from None
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="strict", secure=False,
        max_age=int(SESSION_DURATION.total_seconds()),
    )
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_auth)) -> dict:
    return {"username": username}
