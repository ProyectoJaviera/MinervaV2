"""Esquema de salida del LLM, validado con pydantic (subfase 3.6, CLAUDE.md:
"Toda decision del LLM se valida con esquema"). Ver docs/FASE3_6_LLM.md, seccion (b).

`extra="forbid"` y los limites de `razonamiento`/`confianza` hacen que cualquier
campo extra, fuera de rango, o texto antes/despues del JSON se trate como
respuesta invalida (etiqueta SIN_LLM, nunca RECHAZADA -- ver seccion (d))."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

APROBAR = "APROBAR"
RECHAZAR = "RECHAZAR"

# Mapeo fijo decision -> etiqueta de la sombra (seccion b).
DECISION_TO_LABEL = {APROBAR: "APROBADA", RECHAZAR: "RECHAZADA"}


class LlmDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APROBAR", "RECHAZAR"]
    confianza: float = Field(ge=0.0, le=1.0)
    razonamiento: str = Field(min_length=1, max_length=300)
