"""Esquema de salida del LLM (subfase 3.6, fase i): JSON malformado, campo
extra, fuera de rango o texto fuera del JSON son invalidos por diseno."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.llm.schemas import (
    DECISION_TO_LABEL,
    RAZONAMIENTO_MAX_LENGTH,
    LlmDecisionOut,
    parse_decision,
)


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
        '```json\n{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"}\n```',
        '```\n{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"}\n```',
    ],
)
def test_invalid_payloads_are_rejected(raw):
    with pytest.raises(ValidationError):
        LlmDecisionOut.model_validate_json(raw)


# --- parse_decision: revision del piloto v1 (docs/FASE3_6_LLM.md seccion k) --


def test_parse_decision_truncates_an_overly_long_razonamiento_instead_of_rejecting_it():
    raw = json.dumps({
        "decision": "APROBAR", "confianza": 0.8, "razonamiento": "x" * 400,
    })
    result = parse_decision(raw)
    assert result.decision.decision == "APROBAR"
    assert len(result.decision.razonamiento) == RAZONAMIENTO_MAX_LENGTH
    assert result.repaired is False  # JSON valido: no hizo falta el reparo


def test_parse_decision_does_not_touch_a_razonamiento_already_within_the_limit():
    raw = json.dumps({"decision": "RECHAZAR", "confianza": 0.3, "razonamiento": "corto"})
    result = parse_decision(raw)
    assert result.decision.razonamiento == "corto"
    assert result.repaired is False


def test_parse_decision_still_raises_on_malformed_json():
    with pytest.raises(json.JSONDecodeError):
        parse_decision('{"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"')


# --- reparo MUY acotado: razonamiento sin cerrar (piloto v2, 2/31 casos) ----
# Las dos cadenas de abajo son las REALES de `llm_logs.response_raw` en
# data/piloto_v2.db (ids 2 y 28, leida en modo solo lectura), no inventadas.

_REAL_UNTERMINATED_CASE_1 = (
    '{"decision": "APROBAR", "confianza": 0.6, "razonamiento": "Riesgo principal: '
    'correlación 0.90 con BTC y señal de cruce EMA débil (spread ~0.1%) en 4h. '
    'Compensa: tendencia 1h alcista (+2.5% 24h/72h, +1.5% sobre EMA50), funding '
    'negativo a favor del LONG, R:R 2:1, sin posiciones abiertas.}'
)
_REAL_UNTERMINATED_CASE_2 = (
    '{"decision": "APROBAR", "confianza": 0.6, "razonamiento": "Riesgo principal: '
    'funding -0.025% (shorts pagan) y rotura apenas bajo el mínimo Donchian en TRX '
    'de baja volatilidad. Compensa: SHORT a favor de tendencia bajista (24h/72h '
    'negativos, bajo EMA50), R/R 3.6, SL 27.6% del margen, sin posiciones abiertas.}'
)


@pytest.mark.parametrize("raw", [_REAL_UNTERMINATED_CASE_1, _REAL_UNTERMINATED_CASE_2])
def test_parse_decision_repairs_the_two_real_unterminated_razonamiento_cases(raw):
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)  # confirma que de verdad es el mismo defecto real

    result = parse_decision(raw)

    assert result.repaired is True
    assert result.decision.decision == "APROBAR"
    assert result.decision.confianza == pytest.approx(0.6)
    assert result.decision.razonamiento.startswith("Riesgo principal:")
    assert result.decision.razonamiento.endswith("abiertas.")  # sin la llave final


def test_repair_does_not_trigger_for_a_different_kind_of_broken_json():
    """El reparo es MUY acotado: solo la forma exacta de razonamiento sin
    cerrar. Un orden de campos distinto (aunque tambien le falte la comilla)
    no debe repararse -- sigue INVALID."""
    raw = (
        '{"confianza": 0.6, "decision": "APROBAR", "razonamiento": '
        '"orden de campos distinto, sin comilla de cierre.}'
    )
    with pytest.raises(json.JSONDecodeError):
        parse_decision(raw)


def test_repair_does_not_trigger_when_decision_is_also_malformed():
    raw = '{"decision": "TAL_VEZ", "confianza": 0.6, "razonamiento": "sin cerrar.}'
    with pytest.raises(json.JSONDecodeError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"decision": "APROBAR", "confianza": 0.8, "razonamiento": "x" * 400,
                    "extra": 1}),  # campo extra: sigue invalido aunque se trunque razonamiento
        json.dumps({"decision": "TAL_VEZ", "confianza": 0.8, "razonamiento": "x" * 400}),
        json.dumps({"decision": "APROBAR", "confianza": 1.5, "razonamiento": "x" * 400}),
        json.dumps({"decision": "APROBAR", "confianza": 0.8, "razonamiento": ""}),
        'Aqui esta mi respuesta: {"decision": "APROBAR", "confianza": 0.8, "razonamiento": "ok"}',
        '["no", "es", "un", "objeto"]',
    ],
)
def test_parse_decision_still_rejects_broken_schema_even_with_a_long_razonamiento(raw):
    with pytest.raises((ValidationError, json.JSONDecodeError)):
        parse_decision(raw)
