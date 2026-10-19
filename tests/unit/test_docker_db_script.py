"""Fase 7: `scripts/docker_db.py` (respaldar, exportar y correr informes sobre la base que vive
en el volumen de Docker) y `scripts/db_fingerprint.py` (la huella con la que la mudanza
demuestra que no se perdio nada). Contra el `docker` falso de tests/support: sin Docker y sin
tocar `data/`."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import db_fingerprint, docker_db
from tests.support.fakes import FakeDocker, make_native_db, table_counts

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    fd = FakeDocker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(fd.state_dir))
    monkeypatch.setenv("MINERVA_DOCKER_EXE", str(fd.cmd))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    yield fd
    fd.shutdown()


def seeded(fake: FakeDocker, tmp_path: Path, service: str = "running") -> Path:
    source = make_native_db(tmp_path / "origen.db", {"shadow_trades": 6, "llm_logs": 4})
    fake.seed_volume(source)
    fake.update(service=service)
    return source


# --- db_fingerprint ------------------------------------------------------------------------------


def test_the_fingerprint_counts_rows_checks_integrity_and_summarizes_the_schema(tmp_path):
    db = make_native_db(tmp_path / "a.db", {"shadow_trades": 6, "llm_logs": 4})

    result = db_fingerprint.fingerprint(db)

    assert result["integrity"] == "ok"
    assert result["tables"]["shadow_trades"] == 6 and result["tables"]["llm_logs"] == 4
    assert len(result["schema_sha256"]) == 64
    assert result["journal_mode"] == "wal"


def test_two_equal_databases_have_no_differences_and_any_change_is_reported(tmp_path):
    left = db_fingerprint.fingerprint(make_native_db(tmp_path / "a.db", {"shadow_trades": 6}))
    same = db_fingerprint.fingerprint(make_native_db(tmp_path / "b.db", {"shadow_trades": 6}))
    other = db_fingerprint.fingerprint(make_native_db(tmp_path / "c.db", {"shadow_trades": 7}))

    assert db_fingerprint.differences(left, same) == []
    assert db_fingerprint.differences(left, other) == ["shadow_trades: origen 6 filas, destino 7"]


def test_a_different_schema_or_a_missing_table_is_a_difference(tmp_path):
    db = make_native_db(tmp_path / "a.db")
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE extra (id INTEGER)")
    conn.commit()
    conn.close()
    base = db_fingerprint.fingerprint(make_native_db(tmp_path / "b.db"))
    extra = db_fingerprint.fingerprint(db)

    found = db_fingerprint.differences(base, extra)

    assert "el esquema es distinto" in found and "extra: origen None filas, destino 0" in found


def test_the_journal_mode_is_not_a_difference(tmp_path):
    wal = db_fingerprint.fingerprint(make_native_db(tmp_path / "a.db"))
    classic = dict(wal, journal_mode="delete")

    assert db_fingerprint.differences(wal, classic) == []


def test_a_failed_integrity_check_is_a_difference():
    bad = {"integrity": "row 3 missing", "schema_sha256": "x", "tables": {"t": 1}}
    good = {"integrity": "ok", "schema_sha256": "x", "tables": {"t": 1}}

    assert any("integridad del origen" in d for d in db_fingerprint.differences(bad, good))
    assert any("integridad del destino" in d for d in db_fingerprint.differences(good, bad))


def test_the_fingerprint_cli_prints_one_json_line_and_exits_zero(tmp_path):
    db = make_native_db(tmp_path / "a.db", {"shadow_trades": 2})

    result = subprocess.run(
        [sys.executable, "-I", str(REPO_ROOT / "scripts" / "db_fingerprint.py"), "--db", str(db)],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1
    assert json.loads(result.stdout)["tables"]["shadow_trades"] == 2


def test_the_fingerprint_cli_reports_a_missing_database(tmp_path, capsys):
    code = db_fingerprint.main(["--db", str(tmp_path / "no.db")])

    assert code == db_fingerprint.EXIT_NO_DB and "No existe la base" in capsys.readouterr().err


def test_the_fingerprint_never_reads_results_only_counts(tmp_path):
    # La huella es segura con la medicion ciega: solo cuenta filas. Ningun valor de ninguna columna
    # puede aparecer en su salida.
    db = make_native_db(tmp_path / "a.db")
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO trades (valor) VALUES ('PNL-SECRETO-12345')")
    conn.commit()
    conn.close()

    printed = json.dumps(db_fingerprint.fingerprint(db))

    assert "PNL-SECRETO-12345" not in printed


# --- docker_db backup ------------------------------------------------------------------------


def test_backup_keeps_a_copy_in_the_volume_and_one_on_the_host_and_prunes_both(
    fake, tmp_path, capsys,
):
    seeded(fake, tmp_path)
    out = tmp_path / "respaldos"
    out.mkdir()
    for name in ("docker_20200101_000000.db", "docker_20200102_000000.db"):
        (out / name).write_bytes(b"viejo")

    code = docker_db.main(["backup", "--out-dir", str(out), "--keep", "2"])

    assert code == 0
    host = sorted(p.name for p in out.glob("docker_*.db"))
    assert len(host) == 2 and "docker_20200101_000000.db" not in host  # el mas viejo se borro
    newest = host[-1]
    assert table_counts(out / newest)["shadow_trades"] == 6
    assert (fake.volume_dir / "backups" / newest).exists()  # y tambien queda en el volumen
    text = capsys.readouterr().out
    assert "Respaldo en el volumen" in text and "SHA-256 verificado" in text
    assert "Borrado por antiguedad (--keep 2): docker_20200101_000000.db" in text


def test_backup_rejects_a_negative_keep_and_a_dangerous_prefix(fake):
    for args in (["backup", "--keep", "-1"], ["backup", "--prefix", "../fuera"]):
        with pytest.raises(SystemExit) as exc:
            docker_db.main(args)
        assert exc.value.code == 2


def test_docker_trouble_is_an_error_message_and_exit_code_five_not_a_traceback(
    fake, tmp_path, capsys,
):
    seeded(fake, tmp_path)
    fake.fail_when("backup_db.py")

    code = docker_db.main(["backup", "--out-dir", str(tmp_path / "r")])

    assert code == docker_db.EXIT_DOCKER and "ERROR:" in capsys.readouterr().err


# --- docker_db export ------------------------------------------------------------------------


def test_export_prints_only_the_path_so_other_scripts_can_read_it(fake, tmp_path, capsys):
    seeded(fake, tmp_path)

    code = docker_db.main(
        ["export", "--out-dir", str(tmp_path / "salida"), "--prefix", "veredicto"],
    )

    assert code == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1
    copy = Path(printed[0])
    assert copy.parent == tmp_path / "salida" and copy.name.startswith("veredicto_")
    assert table_counts(copy)["llm_logs"] == 4


# --- docker_db run ---------------------------------------------------------------------------


def test_run_gives_the_script_a_fresh_copy_forwards_its_arguments_and_deletes_the_copy(
    fake, tmp_path, monkeypatch, capfd,
):
    seeded(fake, tmp_path)
    monkeypatch.setattr(docker_db.tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()

    code = docker_db.main(["run", "db_fingerprint.py"])

    assert code == 0
    printed = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert printed["tables"]["shadow_trades"] == 6
    assert list((tmp_path / "tmp").iterdir()) == []  # la copia de 160 MB no se queda


def test_run_returns_the_exit_code_of_the_script(fake, tmp_path):
    seeded(fake, tmp_path)

    # El ultimo `--db` gana: apunta a una base que no existe y el script sale con su codigo 3.
    code = docker_db.main(["run", "db_fingerprint.py", "--", "--db", str(tmp_path / "otra.db")])

    assert code == db_fingerprint.EXIT_NO_DB  # el codigo del script se devuelve tal cual


@pytest.mark.parametrize("name", ["../secreto.py", "run.sh", "con espacio.py", "a/b.py", "X.py"])
def test_run_only_accepts_plain_script_names_from_the_scripts_folder(fake, name, capsys):
    code = docker_db.main(["run", name])

    assert code == docker_db.EXIT_USAGE
    assert "no valido" in capsys.readouterr().err


def test_run_refuses_a_script_that_does_not_exist(fake, capsys):
    code = docker_db.main(["run", "no_existe_xyz.py"])

    assert code == docker_db.EXIT_USAGE
    assert "No existe scripts/no_existe_xyz.py" in capsys.readouterr().err


def test_run_keep_copy_moves_the_copy_to_the_output_folder(fake, tmp_path):
    seeded(fake, tmp_path)
    out = tmp_path / "guardadas"

    code = docker_db.main(["run", "--keep-copy", "--out-dir", str(out), "db_fingerprint.py"])

    assert code == 0
    kept = list(out.glob("informe_*.db"))
    assert len(kept) == 1 and table_counts(kept[0])["shadow_trades"] == 6


def test_the_tool_runs_standalone_from_another_directory(fake, tmp_path):
    seeded(fake, tmp_path)
    out = tmp_path / "salida"

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "docker_db.py"), "export",
         "--out-dir", str(out)],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env=fake.env() | {"PYTHONIOENCODING": "utf-8"},
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).exists()


def test_the_docker_tool_is_not_shipped_in_the_image_but_its_support_module_is():
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "scripts/docker_db.py" in ignore  # solo tiene sentido en el host
    assert "scripts/docker_support.py" not in ignore  # los scripts de respaldo lo importan
    assert os.path.isfile(REPO_ROOT / "scripts" / "docker_support.py")
