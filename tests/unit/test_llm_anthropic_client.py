"""Construccion del cliente real (subfase 3.6, ajuste 1): sin red, solo
confirma que `max_retries=0` llega al SDK -- un timeout no se multiplica y la
decision no arriesga superar los 60 s de la cuenta real (seccion l)."""

from __future__ import annotations

from app.config import Settings
from app.llm.client import build_anthropic_client


def test_real_client_is_built_with_zero_retries():
    settings = Settings(_env_file=None, ANTHROPIC_API_KEY="sk-test-dummy")
    client = build_anthropic_client(settings)
    assert client._client.max_retries == 0
