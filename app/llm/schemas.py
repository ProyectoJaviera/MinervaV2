"""Esquema de salida del LLM, validado con pydantic (subfase 3.6, CLAUDE.md:
"Toda decision del LLM se valida con esquema"). Ver docs/FASE3_6_LLM.md, seccion (b).

`extra="forbid"` y los limites de `confianza`/`razonamiento` hacen que cualquier
campo extra, fuera de rango, o texto antes/despues del JSON se trate como
respuesta invalida (etiqueta SIN_LLM, nunca RECHAZADA -- ver seccion (d)).

**Revision del piloto v1 (signal_review_v1, docs/FASE3_6_LLM.md seccion k):**
de 21 respuestas, la UNICA invalida fue un `razonamiento` de mas de 300
caracteres con el resto del JSON perfecto (`decision`/`confianza` validos).
Perder una decision entera por un limite de longitud que no afecta la calidad
del juicio es un costo que no corresponde a un fallo real -- el prompt v2 pide
"maximo 250 caracteres" (mas margen), y ademas `parse_decision` TRUNCA en vez
de invalidar. INVALID (SIN_LLM) sigue aplicando SOLO a JSON roto o a
`decision`/`confianza` fuera de esquema -- eso si es una respuesta que no se
puede confiar."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

APROBAR = "APROBAR"
RECHAZAR = "RECHAZAR"

# Mapeo fijo decision -> etiqueta de la sombra (seccion b).
DECISION_TO_LABEL = {APROBAR: "APROBADA", RECHAZAR: "RECHAZADA"}

RAZONAMIENTO_MAX_LENGTH = 300


class LlmDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APROBAR", "RECHAZAR"]
    confianza: float = Field(ge=0.0, le=1.0)
    razonamiento: str = Field(min_length=1, max_length=RAZONAMIENTO_MAX_LENGTH)


def parse_decision(raw_text: str) -> LlmDecisionOut:
    """Valida `raw_text` contra `LlmDecisionOut`, truncando `razonamiento` a
    `RAZONAMIENTO_MAX_LENGTH` ANTES de validar en vez de invalidar toda la
    decision por un limite de longitud (ver el docstring del modulo). Sigue
    lanzando (`json.JSONDecodeError` o `pydantic.ValidationError`, ambas
    capturadas en `decision_service.py` como INVALID) si el JSON esta roto,
    si no es un objeto, o si `decision`/`confianza`/un campo extra no cumplen
    el esquema -- la verdadera "respuesta invalida" sigue siendolo."""
    data = json.loads(raw_text)
    if isinstance(data, dict) and isinstance(data.get("razonamiento"), str):
        data["razonamiento"] = data["razonamiento"][:RAZONAMIENTO_MAX_LENGTH]
    return LlmDecisionOut.model_validate(data)
