"""Fase 6 (limpieza): ningun secreto ni dato local en lo que se versiona.

`CLAUDE.md`: nunca claves en codigo, tests, logs ni commits; nunca subir `.env`, `data/` ni
`logs/`. Esta prueba recorre los archivos que git versionaria (los versionados mas los nuevos no
ignorados) y falla ante:

- un archivo que no debe versionarse (credenciales, bases de datos, logs, claves, certificados);
- un patron de credencial real (claves de Anthropic, tokens de bots de Telegram, hashes bcrypt,
  claves privadas, tokens de GitHub o de AWS);
- una asignacion de un valor largo a una variable de credencial (`ANTHROPIC_API_KEY=...`), salvo en
  las pruebas, donde se aceptan solo valores que se declaran falsos.

Nunca imprime el valor encontrado: solo la regla, el archivo y la linea.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Rutas que NO deben estar versionadas jamas.
FORBIDDEN_PATHS = (
    re.compile(r"(^|/)\.env($|\.(?!example$))"),
    re.compile(r"^data/"),
    re.compile(r"^logs/"),
    re.compile(r"\.(db|db-wal|db-shm|sqlite3?)$"),
    re.compile(r"\.(pem|key|p12|pfx)$"),
    re.compile(r"^docker/certs/.+\.(crt|cer|pem|key)$"),
    re.compile(r"(^|/)settings\.local"),
)

# Patrones de credenciales reales: valen en TODOS los archivos, pruebas incluidas.
SECRET_PATTERNS = {
    "clave de Anthropic": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "token de bot de Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "hash bcrypt": re.compile(r"\$2[aby]?\$\d{2}\$[./A-Za-z0-9]{40,}"),
    "clave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "token de GitHub": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "clave de AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}

# `NOMBRE_DE_CREDENCIAL = "valor largo"` o `"NOMBRE": "valor largo"` (literal entre comillas).
CREDENTIAL_NAME = r"[A-Z0-9_]*(?:API_KEY|BOT_TOKEN|JWT_SECRET|PASSWORD_HASH|SECRET_KEY)"
QUOTED_ASSIGNMENT = re.compile(
    rf"""(?i)\b{CREDENTIAL_NAME}\b["']?\s*[=:]\s*["']([A-Za-z0-9+/_=.$-]{{12,}})["']"""
)
# Estilo de archivo de entorno (`NOMBRE=valor`), solo en archivos que no son Python.
ENV_ASSIGNMENT = re.compile(rf"""(?im)^\s*(?:export\s+)?{CREDENTIAL_NAME}=([^\s#"']{{12,}})""")
# En las pruebas se aceptan valores que se declaran falsos.
FAKE_MARKERS = ("prueba", "test", "dummy", "fake", "falso", "ejemplo", "example", "e2e", "xxxx")


def is_forbidden_path(path: str) -> bool:
    return any(rx.search(path) for rx in FORBIDDEN_PATHS)


def scan_text(path: str, text: str) -> list[str]:
    """Hallazgos como 'regla @ archivo:linea' (sin el valor encontrado)."""
    in_tests = path.startswith("tests/")
    assignments = [QUOTED_ASSIGNMENT]
    if not path.endswith(".py"):
        assignments.append(ENV_ASSIGNMENT)
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, rx in SECRET_PATTERNS.items():
            if rx.search(line):
                findings.append(f"{rule} @ {path}:{number}")
        for rx in assignments:
            for match in rx.finditer(line):
                value = match.group(1).lower()
                if in_tests and any(marker in value for marker in FAKE_MARKERS):
                    continue
                findings.append(f"credencial asignada @ {path}:{number}")
    return findings


def files_git_would_commit() -> list[str]:
    """Versionados + nuevos no ignorados, que existan en disco."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, check=True, timeout=120,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git no esta disponible o esto no es un repositorio")
    paths = sorted({p for p in listed.split("\0") if p})
    return [p for p in paths if (REPO_ROOT / p).is_file()]


def read_text_or_none(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data[:8000]:
        return None  # binario (imagenes, etc.)
    return data.decode("utf-8", errors="replace")


# --- el repositorio real --------------------------------------------------------------------


def test_nothing_forbidden_is_versioned():
    forbidden = [p for p in files_git_would_commit() if is_forbidden_path(p)]

    assert not forbidden, f"archivos que no deben versionarse: {forbidden}"


def test_no_secret_is_versioned():
    findings = []
    for path in files_git_would_commit():
        text = read_text_or_none(REPO_ROOT / path)
        if text is not None:
            findings += scan_text(path, text)

    assert not findings, "posibles credenciales (valor no mostrado): " + "; ".join(findings)


def test_the_template_is_versioned_and_the_real_env_is_ignored():
    paths = files_git_would_commit()

    assert ".env.example" in paths
    assert ".env" not in paths


@pytest.mark.parametrize("path", [
    ".env", ".env.local", ".env.production", "data/minerva_estabilidad.db",
    "data/backups/minerva_20261001_000000.db", "logs/minerva.log", "logs/medicion_out.log",
    "otra.db", "otra.db-wal", "docker/certs/red-corporativa.crt", ".claude/settings.local.json",
    "frontend/dist/index.html",
])
def test_gitignore_covers_what_must_never_be_pushed(path):
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", path],
        capture_output=True, timeout=60,
    )

    assert result.returncode == 0, f"{path} no esta en .gitignore"


@pytest.mark.parametrize(
    "path", [".env.example", "docker/certs/README.md", "docs/INFORME_FINAL.md"]
)
def test_gitignore_does_not_hide_what_must_be_versioned(path):
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", path],
        capture_output=True, timeout=60,
    )

    assert result.returncode == 1, f"{path} esta ignorado y deberia versionarse"


# --- el escaner mismo (muestras armadas en tiempo de ejecucion: este archivo no las contiene) ---


def test_the_scanner_catches_each_kind_of_secret():
    samples = {
        "clave de Anthropic": "key = '" + "sk-ant-" + "a" * 30 + "'",
        "token de bot de Telegram": "t = " + "1234567890" + ":" + "A" * 35,
        "hash bcrypt": "h = " + "$2b$12$" + "a" * 53,
        "clave privada": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "token de GitHub": "g = " + "ghp_" + "a" * 36,
        "clave de AWS": "a = " + "AKIA" + "A" * 16,
    }

    for rule, line in samples.items():
        findings = scan_text("app/x.py", line)
        assert any(f.startswith(rule) for f in findings), rule


def test_the_scanner_flags_a_long_assigned_credential_outside_tests():
    line = "ANTHROPIC_API_KEY = " + '"' + "abcdefghijklmnopqrstuvwxyz" + '"'

    assert scan_text("app/x.py", line) == ["credencial asignada @ app/x.py:1"]
    assert scan_text("docs/x.md", "JWT_SECRET=" + "abcdefghijklmnopqrstuv")


def test_the_scanner_accepts_declared_fakes_only_inside_tests():
    fake = 'JWT_SECRET = "un-secreto-de-prueba-bien-largo"'

    assert scan_text("tests/unit/test_auth.py", fake) == []
    assert scan_text("app/x.py", fake) != []


def test_the_scanner_ignores_empty_short_and_placeholder_values():
    for line in (
        "ANTHROPIC_API_KEY=", "AUTH_PASSWORD_HASH=...", "JWT_SECRET=<cadena aleatoria>",
        'api_key = ""', "TELEGRAM_BOT_TOKEN: str = Field(default='')",
    ):
        assert scan_text("docs/x.md", line) == [], line


def test_the_scanner_never_reports_the_value():
    secret = "sk-ant-" + "Z" * 40

    findings = scan_text("app/x.py", f"x = '{secret}'")

    assert findings and all(secret not in finding for finding in findings)


def test_forbidden_paths_rules():
    for path in (".env", ".env.local", "data/x", "logs/y.log", "a.db", "a.db-wal", "k.pem",
                 "docker/certs/c.crt", ".claude/settings.local.json"):
        assert is_forbidden_path(path), path
    for path in (".env.example", "docker/certs/README.md", "app/main.py", "docs/MANUAL.md"):
        assert not is_forbidden_path(path), path
