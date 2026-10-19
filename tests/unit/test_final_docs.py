"""Fase 6: la documentacion final no se queda atras del codigo.

- Los enlaces relativos y las rutas de archivos que citan los documentos VIVOS (README, manual de
  operacion, informe final, migracion y procedimiento del veredicto) existen.
- Todo script de `scripts/` esta descrito en el manual de operacion.
- El informe final no conserva marcadores sin rellenar.

Los documentos historicos (`PROGRESS.md`, `docs/FASE*_PLAN.md`) citan archivos que ya no existen
(p. ej. `docker/Dockerfile.backend`) y NO se revisan: son una bitacora, no una guia.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVING_DOCS = (
    "README.md", "docs/MANUAL_OPERACION.md", "docs/INFORME_FINAL.md", "docs/MIGRACION_PC.md",
    "docs/VEREDICTO_3_6.md", "docs/ESQUEMA_BD.md", "docs/MUDANZA_DOCKER.md",
)

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_SPAN_PATH = re.compile(
    r"`((?:scripts|docs|app|tests|docker|frontend)/[A-Za-z0-9_./-]+\.(?:py|md|ps1|yml|txt|lock|tsx?))`"
)
# Cosas que son plantillas o ejemplos, no archivos reales.
TEMPLATE_HINTS = ("<", "*", "AAAAMMDD", "_fecha", "<fecha>")


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", LIVING_DOCS)
def test_markdown_links_point_to_existing_files(relative):
    base = (REPO_ROOT / relative).parent
    broken = []
    for target in MARKDOWN_LINK.findall(read(relative)):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if path and not (base / path).exists():
            broken.append(target)

    assert not broken, f"enlaces rotos en {relative}: {broken}"


@pytest.mark.parametrize("relative", LIVING_DOCS)
def test_cited_repository_paths_exist(relative):
    missing = []
    for cited in sorted(set(CODE_SPAN_PATH.findall(read(relative)))):
        if any(hint in cited for hint in TEMPLATE_HINTS):
            continue
        if not (REPO_ROOT / cited).exists():
            missing.append(cited)

    assert not missing, f"{relative} cita archivos que no existen: {missing}"


def test_every_script_is_described_in_the_operations_manual():
    manual = read("docs/MANUAL_OPERACION.md")
    undocumented = sorted(
        script.name for script in (REPO_ROOT / "scripts").iterdir()
        if script.is_file() and script.suffix in (".py", ".ps1") and script.name not in manual
        and script.stem not in manual
    )

    assert not undocumented, f"scripts sin describir en el manual: {undocumented}"


def test_the_final_report_has_no_unfilled_placeholders():
    report = read("docs/INFORME_FINAL.md")

    assert "{{" not in report and "}}" not in report, "quedan marcadores {{...}} sin rellenar"
    assert "[[PENDIENTE" not in report, "quedan marcadores [[PENDIENTE...]] sin rellenar"


def test_the_readme_opens_with_the_risk_notice():
    readme = read("README.md")

    assert "puede perder dinero" in readme[:1500]
    assert "nada de lo que hay aquí garantiza ganancias" in readme[:1500]


def test_the_final_report_states_what_is_not_verified():
    report = read("docs/INFORME_FINAL.md")

    for heading in ("Qué está verificado y qué no", "No verificado"):
        assert heading in report
    # Lo que nunca debe omitirse del informe: lo no probado de verdad.
    assert "Docker con un daemon real" in report
    assert "Entrega real de mensajes de Telegram" in report
    # Fase 7: lo que sigue sin probarse de la mudanza a Docker
    for gap in (
        "la mudanza real", "Docker Desktop inicie con Windows", "Ctrl+C a mitad de la mudanza",
    ):
        assert gap in report, gap


def test_the_docker_migration_guide_explains_how_to_run_and_undo_it():
    guide = read("docs/MUDANZA_DOCKER.md")

    for needed in (
        "migrate_to_docker.ps1 -DryRun", "migrate_to_docker.ps1", "rollback_from_docker.ps1",
        "MIGRAR", "VOLVER", "Lo que falló", "Lo que no se pudo verificar", "unless-stopped",
        "Start Docker Desktop when you sign in",
    ):
        assert needed in guide, needed
