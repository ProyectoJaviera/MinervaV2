"""Test de integracion manual de `scripts/start_bot.ps1`/`stop_bot.ps1`
(continuidad operativa, plan pre-Fase 5 aprobado 2026-10-14). Corre el
lifespan REAL completo (descubrimiento de universo, backfill de velas) --
lento y contra la red real, nunca en CI por defecto. SIEMPRE contra una
copia temporal de la base y el puerto 8001 -- jamas el puerto 8000 ni
`data/minerva_estabilidad.db`.

Ejecutar a mano con:
    MINERVA_RUN_LIVE_TESTS=1 pytest tests/integration/test_start_stop_bot_scripts.py -m slow
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PORT = 8001

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MINERVA_RUN_LIVE_TESTS") != "1",
        reason="Test de integracion contra la red real; se omite por defecto.",
    ),
    pytest.mark.skipif(
        sys.platform != "win32", reason="start_bot.ps1/stop_bot.ps1 son de Windows.",
    ),
]


def _run_ps1(script: str, *args: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(REPO_ROOT / "scripts" / script), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _wait_for_health(port: int, timeout_s: float = 90.0) -> dict | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5.0)
            response.raise_for_status()
            return response.json()
        except Exception:  # noqa: BLE001 - reintenta hasta el timeout
            time.sleep(2.0)
    return None


@pytest.fixture
def real_db_copy_for_bot(tmp_path):
    """Copia consistente (API de backup de sqlite, no shutil.copy2) de la
    base real -- assert de ruta explicito, como el resto de scripts que
    tocan datos reales."""
    import sqlite3

    # La base que usa start_bot.ps1 en el bot real, NO `settings.database_path`:
    # el default de config.py (data/minerva.db) es una base vieja y un backfill en
    # frio de dias hace que /health tarde mas de 180 s en responder.
    real_path = (REPO_ROOT / "data" / "minerva_estabilidad.db").resolve()
    copy_path = tmp_path / "test_bot_copy.db"
    assert copy_path.resolve() != real_path, "la copia no puede ser la base real"
    if real_path.exists():
        source = sqlite3.connect(f"file:{real_path.as_posix()}?mode=ro", uri=True)
        dest = sqlite3.connect(str(copy_path))
        source.backup(dest)
        dest.close()
        source.close()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    yield copy_path, log_dir
    # Red de seguridad: si el test falla a mitad de camino, no debe quedar
    # nada corriendo en el puerto 8001.
    _run_ps1("stop_bot.ps1", "-Port", str(PORT), timeout=30)


@pytest.mark.slow
def test_start_refuses_a_second_instance_then_stop_frees_the_port(real_db_copy_for_bot):
    db_copy, log_dir = real_db_copy_for_bot

    first = _run_ps1(
        # 210s: mas que el deadline interno de start_bot.ps1 (180s) mas
        # margen -- un backfill de velas en frio puede tardar mas de 60s.
        "start_bot.ps1", "-Port", str(PORT), "-DatabasePath", str(db_copy),
        "-LogDir", str(log_dir), timeout=210,
    )
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    health = _wait_for_health(PORT)
    assert health is not None, "/health nunca respondio"
    assert health["status"] == "ok"
    assert health["mode"] == "PAPER_TRADING"
    # Solo el bot real (8000) arma el brazo ejecutado de la cuenta paper (subfase 3.9): en el
    # 8001 el interruptor queda apagado y no se escribe ninguna marca de activacion.
    assert health["paper_execution"] == {
        "enabled": False, "active": False, "armed_since": None, "opened_today": 0,
        "skipped_today": 0,
    }
    assert health["llm_labeling"]["enabled"] is False
    diary = (log_dir / "medicion.log").read_text(encoding="utf-8")
    assert "PAPER_EXECUTION_ENABLED=false" in diary

    second = _run_ps1(
        "start_bot.ps1", "-Port", str(PORT), "-DatabasePath", str(db_copy),
        "-LogDir", str(log_dir), timeout=30,
    )
    assert second.returncode == 1  # rechazado: ya hay algo en el puerto
    assert "RECHAZADO" in second.stdout

    stop = _run_ps1("stop_bot.ps1", "-Port", str(PORT), timeout=30)
    assert stop.returncode == 0, f"stdout={stop.stdout!r} stderr={stop.stderr!r}"
    assert "quedo libre" in stop.stdout
    # La cadena completa cmd.exe -> pythonw.exe del .venv -> proceso real se reconoce,
    # se detiene entera y no queda vivo ninguno de los tres.
    chain_line = next(
        line for line in stop.stdout.splitlines() if line.startswith("Cadena reconocida")
    )
    assert "proceso real" in chain_line
    assert "launcher del venv (pythonw.exe)" in chain_line
    assert "cmd.exe de start_bot.ps1" in chain_line
    pids = [int(p) for p in re.findall(r"PID (\d+)", chain_line)]
    assert len(pids) == 3
    assert "Confirmado: no queda vivo ningun proceso de la cadena" in stop.stdout
    for pid in pids:
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
        ).stdout
        assert str(pid) not in listing, f"el PID {pid} sigue vivo despues de stop_bot.ps1"

    deadline = time.monotonic() + 10
    freed = False
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=1.0)
        except Exception:  # noqa: BLE001 - justo lo que se espera tras detenerlo
            freed = True
            break
        time.sleep(0.5)
    assert freed, "el puerto 8001 sigue respondiendo despues de stop_bot.ps1"


@pytest.mark.slow
def test_stop_with_nothing_listening_is_a_clean_no_op():
    result = _run_ps1("stop_bot.ps1", "-Port", str(PORT), timeout=15)

    assert result.returncode == 0
    assert "no hay nada que detener" in result.stdout
