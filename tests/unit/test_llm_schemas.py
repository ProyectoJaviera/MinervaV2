"""Esquema de salida del LLM (subfase 3.6, fase i): JSON malformado, campo
extra, fuera de rango o texto fuera del JSON son invalidos por diseno."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.schemas import DECISION_TO_LABEL, LlmDecisionOut


def test_valid_json_parses():
    out = LlmDecisionOut.model_validate_json(
        '{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"}'
    )
    assert out.decision == "APROBAR" and out.confianza == 0.8


def test_decision_maps_to_the_shadow_label():
    assert DECISION_TO_LABEL["APROBAR"] == "APROBADA"
    assert DECISION_TO_LABEL["RECHAZAR"] == "RECHAZADA"


@pytest.mark.parametrize(
    "raw",
    [
        '{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"',  # JSON malformado
        '{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok", "extra": 1}',  # extra
        '{"decision": "TAL_VEZ", "confianza": 0.8, "razonamiento": "ok"}',  # valor fuera del enum
        '{"decision": "APROBAR", "confianza": 1.5, "razonamiento": "ok"}',  # fuera de rango
        '{"decision": "APROBAR", "confianza": 0.8, "razonamiento": ""}',  # vacio
        '{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "' + ("x" * 301) + '"}',  # >300
        'Aqui esta mi respuesta: {"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"}',
        '{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"} y esto no deberia estar',
    ],
)
def test_invalid_payloads_are_rejected(raw):
    with pytest.raises(ValidationError):
        LlmDecisionOut.model_validate_json(raw)
