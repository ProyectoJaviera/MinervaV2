"""Campos de `llm_logs` que hay que RECUPERAR del prompt guardado.

Son funciones puras, sin acceso a la base ni a nada del nucleo: las usan el informe del
veredicto (`app/trading/ai_value_report.py`) y la memoria de lecciones
(`app/memory/entries.py`) sin acoplarse entre si.

El etiquetador en vivo no pasa `atr_pct` a `LlmDecisionService.decide_group`, asi que la
columna `llm_logs.atr_pct` queda nula en las filas de MEDICION. El dato sigue ahi: el
mensaje completo que recibio el modelo (sistema, dos saltos de linea y el mensaje como
JSON de una sola linea) queda en la columna `prompt`, y trae `volatility_atr14_pct`: el
MISMO valor que habria ido a la columna. Leerlo de ahi es solo lectura: no cambia lo que
se escribe, ni el prompt, ni las etiquetas.
"""

from __future__ import annotations

import json
import math

ATR_KEY = "volatility_atr14_pct"
_SEPARATOR = "\n\n{"


def atr_from_prompt(prompt: str | None) -> float | None:
    """`volatility_atr14_pct` del mensaje de usuario guardado en `prompt`.

    Nunca lanza.

    Args:
        prompt: el texto completo guardado en `llm_logs.prompt`, o `None`.

    Returns:
        El ATR en porcentaje, o `None` si no hay mensaje legible o el valor no es un numero
        finito y no negativo.
    """
    if not prompt:
        return None
    _, separator, tail = prompt.rpartition(_SEPARATOR)
    if not separator:
        return None
    try:
        message = json.loads("{" + tail)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    value = message.get(ATR_KEY)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def atr_value(atr_column: float | None, prompt: str | None) -> float | None:
    """El ATR de una fila de `llm_logs`: la columna si esta, y si no el del prompt guardado.

    Args:
        atr_column: valor de `llm_logs.atr_pct`, o `None`.
        prompt: texto de `llm_logs.prompt`, o `None`.

    Returns:
        El ATR en porcentaje, o `None` si no se puede recuperar.
    """
    if atr_column is not None:
        return atr_column
    return atr_from_prompt(prompt)
