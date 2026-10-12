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
puede confiar.

**Revision del piloto v2 (signal_review_v2, misma seccion k):** 2 de 31
respuestas (6,5 %) fallaron con el MISMO error de sintaxis -- el modelo omite
la comilla de cierre de `razonamiento` justo antes de la llave final
(`"...sin posiciones abiertas.}"`, `json.JSONDecodeError: "Unterminated
string..."`). Las dos eran APROBAR, y perder decisiones por este patron no es
aleatorio (sesga la medicion) -- `parse_decision` ahora intenta un reparo MUY
acotado (`_try_repair_unterminated_razonamiento`) SOLO para esta forma exacta:
`decision` y `confianza` ya parsearon bien (estan antes del campo roto en el
esquema), y lo unico que falta es la comilla de `razonamiento`. Cualquier otro
JSON roto (orden de campos distinto, mas de un campo faltante, etc.) sigue sin
reparar -- `parse_decision` devuelve `repaired=True` para que el llamador lo
cuente y lo pueda auditar (`llm_logs.json_repaired`, columna nueva).

**Salidas estructuradas (output_config.format=json_schema), investigado antes
de escribir este parser (docs/FASE3_6_LLM.md seccion k, con cita):** estan
disponibles como GA para `claude-sonnet-5-5` (platform.claude.com/docs/en/
build-with-claude/structured-outputs lista el modelo soportado). Pero NINGUNA
pagina oficial confirma ni descarta la combinacion EXACTA con `thinking:
{"type": "between_tools"}` + `output_config.effort="low"` que usa este
proyecto -- la pagina de thinking (.../build-with-claude/thinking) solo dice
que `between_tools` "no acepta ningun otro campo" referido a los campos
PROPIOS de `thinking` (`display`, `budget_tokens`, `block_binding`), no a
`output_config`, que es un objeto hermano. Sin una llamada real de prueba no
se puede confirmar que no de 400 -- y esta revision es en seco, sin llamadas
reales autorizadas. Por eso NO se adopto todavia (se documenta como pendiente,
no se inventa que funciona): el reparo de abajo es la correccion verificada y
sin riesgo para los 2/31 casos reales encontrados."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


@dataclass(frozen=True)
class ParsedDecision:
    decision: LlmDecisionOut
    repaired: bool


# Forma EXACTA del defecto del piloto v2: los tres campos en el orden fijo del
# esquema (seccion b), `decision`/`confianza` ya bien formados, y `razonamiento`
# sin la comilla de cierre antes de la ultima `}` de la respuesta. `re.DOTALL`
# porque el texto de razonamiento puede (en teoria) traer salto de linea.
_UNTERMINATED_RAZONAMIENTO_PATTERN = re.compile(
    r'^\s*\{\s*"decision"\s*:\s*"(APROBAR|RECHAZAR)"\s*,\s*"confianza"\s*:\s*'
    r'(-?\d+(?:\.\d+)?)\s*,\s*"razonamiento"\s*:\s*"(.*)\}\s*$',
    re.DOTALL,
)


def _try_repair_unterminated_razonamiento(raw_text: str) -> LlmDecisionOut | None:
    """`None` si `raw_text` no tiene EXACTAMENTE la forma del defecto descrito
    en el docstring del modulo -- cualquier otra cosa sigue sin repararse."""
    match = _UNTERMINATED_RAZONAMIENTO_PATTERN.match(raw_text)
    if match is None:
        return None
    decision, confianza_raw, razonamiento = match.groups()
    try:
        data = {
            "decision": decision, "confianza": float(confianza_raw),
            "razonamiento": razonamiento[:RAZONAMIENTO_MAX_LENGTH],
        }
        return LlmDecisionOut.model_validate(data)
    except (ValueError, ValidationError):
        return None


def parse_decision(raw_text: str) -> ParsedDecision:
    """Valida `raw_text` contra `LlmDecisionOut`, truncando `razonamiento` a
    `RAZONAMIENTO_MAX_LENGTH` ANTES de validar en vez de invalidar toda la
    decision por un limite de longitud (revision del piloto v1). Si el JSON
    esta roto, intenta el reparo MUY acotado de la revision del piloto v2
    (`_try_repair_unterminated_razonamiento`) antes de rendirse. Sigue
    lanzando (`json.JSONDecodeError` o `pydantic.ValidationError`, ambas
    capturadas en `decision_service.py` como INVALID) si el JSON esta roto de
    cualquier otra forma, si no es un objeto, o si `decision`/`confianza`/un
    campo extra no cumplen el esquema -- la verdadera "respuesta invalida"
    sigue siendolo."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        repaired = _try_repair_unterminated_razonamiento(raw_text)
        if repaired is not None:
            return ParsedDecision(decision=repaired, repaired=True)
        raise
    if isinstance(data, dict) and isinstance(data.get("razonamiento"), str):
        data["razonamiento"] = data["razonamiento"][:RAZONAMIENTO_MAX_LENGTH]
    return ParsedDecision(decision=LlmDecisionOut.model_validate(data), repaired=False)
