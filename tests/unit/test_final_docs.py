"""Fase 6: el informe final y la guia de la mudanza dicen lo que no deben omitir.

Los enlaces, las rutas citadas, las variables, los scripts y las opciones de TODA la documentacion
publica los vigila `tests/unit/test_public_docs.py`. Aqui quedan las comprobaciones de contenido
de dos documentos concretos: el informe final (no deja marcadores sin rellenar y declara lo no
verificado) y la guia de la mudanza a Docker (explica como correrla y deshacerla).

Los documentos historicos (`docs/historial/`) citan archivos que ya no existen (p. ej.
`docker/Dockerfile.backend`) y NO se revisan en cuanto a rutas citadas: son una bitacora, no una
guia.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_the_final_report_has_no_unfilled_placeholders():
    report = read("docs/INFORME_FINAL.md")

    assert "{{" not in report and "}}" not in report, "quedan marcadores {{...}} sin rellenar"
    assert "[[PENDIENTE" not in report, "quedan marcadores [[PENDIENTE...]] sin rellenar"


def test_the_readme_opens_with_the_risk_notice():
    readme = re.sub(r"\s+", " ", read("README.md").replace(">", " "))

    assert "puede perder dinero" in readme[:1800]
    assert "nada de lo que hay aquí garantiza ganancias" in readme[:1800]


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


def test_the_final_report_is_flagged_as_a_snapshot():
    report = read("docs/INFORME_FINAL.md")[:2500]

    assert "fotografía del 2026-10-18" in report.lower()


def test_the_docker_migration_guide_explains_how_to_run_and_undo_it():
    guide = read("docs/MUDANZA_DOCKER.md")

    for needed in (
        "migrate_to_docker.ps1 -DryRun", "migrate_to_docker.ps1", "rollback_from_docker.ps1",
        "MIGRAR", "VOLVER", "Si algo queda a medias", "Lo que no se pudo verificar",
        "unless-stopped", "Start Docker Desktop when you sign in",
    ):
        assert needed in guide, needed


def test_the_original_migration_story_is_kept_in_the_history():
    story = read("docs/historial/MUDANZA_DOCKER_ENSAYO.md")

    assert "Lo que falló y lo que se corrigió" in story
    assert "El ensayo general" in story
