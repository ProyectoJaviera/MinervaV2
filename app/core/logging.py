"""Configuracion de logging para Minerva.

Regla critica (CLAUDE.md): nunca se debe loguear una credencial. Este modulo
no imprime valores de configuracion sensibles; quien agregue logs debe evitar
volcar `settings.anthropic_api_key`, `settings.telegram_bot_token`, etc.
"""

from __future__ import annotations

import logging

from app.config import settings


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    # httpx registra cada peticion a INFO: con LOG_LEVEL=INFO el log se llena de
    # llamadas a la API sin valor operativo. Los avisos y errores siguen visibles.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
