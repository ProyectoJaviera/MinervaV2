"""Login/logout de un solo usuario local (pre-Fase 5, item 2 del plan
aprobado 2026-10-14). Solo abre o cierra una sesion -- nunca controla el bot
(`AUTO_OPEN_WITHOUT_LLM` sigue en `false`, nada de esto es un endpoint de
escritura sobre el trading)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.api.deps import get_settings
from app.api.security import (
    COOKIE_NAME,
    SESSION_DURATION,
    create_session_token,
    require_auth,
    verify_password,
    verify_username,
)
from app.config import Settings

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    valid_username = verify_username(body.username, settings.auth_username)
    valid_password = verify_password(body.password, settings.auth_password_hash)
    if not (valid_username and valid_password):
        # Mensaje generico a proposito: no distingue "usuario no existe" de
        # "contrasena incorrecta".
        raise HTTPException(status_code=401, detail="Usuario o contrasena incorrectos")

    token = create_session_token(body.username, secret=settings.jwt_secret)
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
