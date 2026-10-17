"""Condicion unica para mostrar resultados de las sombras (regla del 2026-10-17):
`is_outcome_reveal_allowed` = gate abierto Y un registro oficial del veredicto en
`docs/VEREDICTO_3_6_REGISTRO*.txt` con un veredicto valido. Abrir el gate no basta."""

from __future__ import annotations

import pytest

from app.trading import verdict_record
from app.trading.measurement_status import MeasurementStatus, is_outcome_reveal_allowed
from app.trading.verdict_record import RECORD_GLOB, find_verdict_record

VALID_REPORT = (
    "Copia analizada: C:\\x.db\n=== Veredicto de valor de la IA (subfase 3.6) ===\n"
    "## Veredicto (bootstrap por conglomerados, regla de la seccion i)\n\n"
    "- Aprobadas: N bruto 310, N efectivo 305\n"
    "- **Veredicto: {verdict}** (intervalo del 95 % por encima de cero)\n"
)

OPEN_STATUS = MeasurementStatus(frozen_hash="h", n_eff_approved=300, n_eff_rejected=300)
CLOSED_STATUS = MeasurementStatus(frozen_hash="h", n_eff_approved=299, n_eff_rejected=300)


def write(docs, name, text):
    (docs / name).write_text(text, encoding="utf-8")


@pytest.fixture
def docs(tmp_path):
    return tmp_path


# --- el archivo del registro --------------------------------------------------------------


def test_no_record_means_nothing_found(docs):
    assert find_verdict_record(docs) is None
    assert find_verdict_record(docs / "no_existe") is None


@pytest.mark.parametrize("verdict", ["APORTA_VALOR", "NO_APORTA_VALOR", "INCONCLUSO"])
def test_each_of_the_three_verdicts_makes_a_valid_record(docs, verdict):
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.txt", VALID_REPORT.format(verdict=verdict))

    assert find_verdict_record(docs).name == "VEREDICTO_3_6_REGISTRO_2026-12-05.txt"


def test_the_undated_name_also_matches_the_pattern(docs):
    write(docs, "VEREDICTO_3_6_REGISTRO.txt", VALID_REPORT.format(verdict="INCONCLUSO"))

    assert find_verdict_record(docs) is not None
    assert RECORD_GLOB == "VEREDICTO_3_6_REGISTRO*.txt"


def test_the_placebo_record_does_not_count_even_if_it_mentions_a_verdict(docs):
    write(docs, "PLACEBO_2026-12-05.txt", VALID_REPORT.format(verdict="APORTA_VALOR"))
    write(docs, "VEREDICTO_3_6.md", VALID_REPORT.format(verdict="APORTA_VALOR"))
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.md", VALID_REPORT.format(verdict="INCONCLUSO"))

    assert find_verdict_record(docs) is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n",
        "GATE CERRADO: no se muestra ningun resultado.",
        "Calibrado: SI",  # un placebo guardado con el nombre equivocado
        "Veredicto: PENDIENTE",
        "Veredicto: APORTA",
        "veredicto APORTA_VALOR",  # sin el prefijo 'Veredicto:'
        "## Desglose por estrategia y por simbolo (solo con APORTA_VALOR)",
        "APORTA_VALOR NO_APORTA_VALOR INCONCLUSO",  # las palabras sueltas no son un veredicto
    ],
)
def test_a_file_without_a_valid_verdict_line_does_not_count(docs, content):
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.txt", content)

    assert find_verdict_record(docs) is None


def test_an_invalid_record_does_not_hide_a_valid_one(docs):
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-01.txt", "basura")
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.txt", VALID_REPORT.format(verdict="INCONCLUSO"))

    assert find_verdict_record(docs).name.endswith("2026-12-05.txt")


def test_a_directory_or_an_oversized_file_with_the_pattern_name_does_not_count(docs):
    (docs / "VEREDICTO_3_6_REGISTRO_dir.txt").mkdir()
    write(
        docs, "VEREDICTO_3_6_REGISTRO_enorme.txt",
        VALID_REPORT.format(verdict="APORTA_VALOR") + "x" * (verdict_record.MAX_RECORD_BYTES + 1),
    )

    assert find_verdict_record(docs) is None


def test_non_utf8_bytes_fail_closed_without_raising(docs):
    (docs / "VEREDICTO_3_6_REGISTRO_raro.txt").write_bytes(b"\xff\xfe\x00\x00 no es texto")

    assert find_verdict_record(docs) is None


# --- la condicion unica ------------------------------------------------------------


def test_gate_closed_reveals_nothing_even_with_a_valid_record(docs):
    report = VALID_REPORT.format(verdict="APORTA_VALOR")
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.txt", report)

    assert is_outcome_reveal_allowed(CLOSED_STATUS, docs) is False


def test_gate_open_without_a_record_reveals_nothing(docs):
    assert is_outcome_reveal_allowed(OPEN_STATUS, docs) is False


def test_gate_open_with_a_record_without_a_valid_verdict_reveals_nothing(docs):
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.txt", "Calibrado: SI\nNO INFORMATIVO")

    assert is_outcome_reveal_allowed(OPEN_STATUS, docs) is False


def test_gate_open_with_a_valid_record_reveals(docs):
    write(docs, "VEREDICTO_3_6_REGISTRO_2026-12-05.txt", VALID_REPORT.format(verdict="INCONCLUSO"))

    assert is_outcome_reveal_allowed(OPEN_STATUS, docs) is True


def test_the_default_docs_dir_is_the_repo_docs_folder_not_the_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # como el Programador de tareas: cwd ajeno

    assert verdict_record.DOCS_DIR.name == "docs"
    assert (verdict_record.DOCS_DIR / "VEREDICTO_3_6.md").exists()


def test_the_function_reads_the_docs_dir_at_call_time(monkeypatch, docs):
    write(docs, "VEREDICTO_3_6_REGISTRO_x.txt", VALID_REPORT.format(verdict="APORTA_VALOR"))
    assert is_outcome_reveal_allowed(OPEN_STATUS, docs) is True

    monkeypatch.setattr(verdict_record, "DOCS_DIR", docs / "otro")
    assert is_outcome_reveal_allowed(OPEN_STATUS) is False  # sin docs_dir usa DOCS_DIR vigente
