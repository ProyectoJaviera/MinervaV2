"""Marcas de tiempo comparables como texto.

Las columnas de tiempo de SQLite son texto ISO 8601: ordenar y comparar con `<=` solo es
correcto si todas llevan el MISMO desfase horario. Todo lo que se escribe o se compara
contra ellas pasa por `iso_utc` (un `datetime` sin zona se toma como UTC), de modo que
un llamador con otro desfase nunca abre una fuga hacia el futuro en los filtros "solo
lo que existia en ese instante".
"""

from __future__ import annotations

from datetime import UTC, datetime


def iso_utc(value: datetime) -> str:
    """Un `datetime` como texto ISO 8601 en UTC.

    Args:
        value: el instante; si no tiene zona horaria se toma como UTC.

    Returns:
        El texto ISO 8601 con desfase `+00:00`.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.isoformat()
