"""Fase 6 (limpieza): `.env.example` es la plantilla que se copia a `.env`.

Debe tener TODAS las variables de `Settings` (ni una de menos ni una de mas: una variable sin
documentar es una que nadie sabe que existe, y una de mas es una que no hace nada), las
credenciales deben ir vacias, y copiarla tal cual debe dar exactamente los valores por defecto del
codigo (la plantilla no cambia el comportamiento en silencio)."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / ".env.example"

# Variables que son (o identifican) credenciales: la plantilla las trae SIEMPRE vacias.
CREDENTIAL_KEYS = frozenset({
    "ANTHROPIC_API_KEY", "COINGECKO_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "AUTH_USERNAME", "AUTH_PASSWORD_HASH", "JWT_SECRET",
})
# Por nombre: cualquier variable futura que parezca una credencial tambien debe ir vacia.
CREDENTIAL_NAME = re.compile(r"(_API_KEY|_BOT_TOKEN|_SECRET|_PASSWORD_HASH)$")


def parse_template() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in TEMPLATE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        assert separator, f"linea sin '=' en .env.example: {line!r}"
        assert name not in values, f"variable repetida en .env.example: {name}"
        values[name.strip()] = value.strip()
    return values


def setting_aliases() -> dict[str, str]:
    """alias de entorno -> nombre del campo."""
    return {field.alias or name.upper(): name for name, field in Settings.model_fields.items()}


def test_every_setting_has_a_line_in_the_template():
    missing = sorted(set(setting_aliases()) - set(parse_template()))

    assert not missing, f"faltan en .env.example: {', '.join(missing)}"


def test_the_template_has_no_variable_the_code_ignores():
    extra = sorted(set(parse_template()) - set(setting_aliases()))

    assert not extra, f"sobran en .env.example (Settings no las lee): {', '.join(extra)}"


def test_credentials_ship_empty():
    values = parse_template()

    for name in CREDENTIAL_KEYS:
        assert name in values, f"{name} debe figurar (vacia) en .env.example"
    filled = sorted(
        name for name, value in values.items()
        if (name in CREDENTIAL_KEYS or CREDENTIAL_NAME.search(name)) and value
    )
    assert not filled, f"credenciales con valor en la plantilla: {', '.join(filled)}"


def test_no_credential_has_a_default_in_the_code():
    aliases = setting_aliases()

    for name in CREDENTIAL_KEYS:
        assert Settings.model_fields[aliases[name]].default == "", name


def test_copying_the_template_gives_exactly_the_code_defaults():
    from_template = Settings(_env_file=str(TEMPLATE))  # type: ignore[call-arg]
    from_code = Settings(_env_file=None)  # type: ignore[call-arg]

    different = sorted(
        name for name in Settings.model_fields
        if getattr(from_template, name) != getattr(from_code, name)
    )
    assert not different, f"la plantilla cambia estos valores respecto al codigo: {different}"


def test_the_template_keeps_the_measurement_switches_off():
    # Lo que `start_bot.ps1` enciende solo con el puerto 8000 NO viene encendido en la plantilla:
    # un `.env` copiado a ciegas nunca gasta API ni compite por el token de Telegram.
    values = parse_template()

    for name in (
        "LLM_LIVE_LABELING_ENABLED", "PAPER_EXECUTION_ENABLED", "NEWS_COLLECTION_ENABLED",
        "LESSONS_MEMORY_ENABLED", "TELEGRAM_COMMANDS_ENABLED", "TELEGRAM_PUSH_ENABLED",
        "AUTO_OPEN_WITHOUT_LLM",
    ):
        assert values[name].lower() == "false", name


def test_the_retired_variables_are_gone_from_the_template():
    # Fase 6: campos que ningun modulo leia (ver el docstring de `app/config.py`).
    retired = {
        "TRADING_SYMBOLS", "KLINE_INTERVAL", "STRATEGY_HISTORY_BARS", "ACTIVE_STRATEGY",
        "MARGIN_MODE", "ANTHROPIC_HAIKU_MODEL", "CONFLUENCE_SCORE_THRESHOLD",
    }

    assert not retired & set(parse_template())
    assert not retired & set(setting_aliases())
