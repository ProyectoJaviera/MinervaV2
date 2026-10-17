"""`scripts/telegram_daily.py`: resumen diario por Telegram. Respuestas de /health
y Telegram SIMULADAS (inyectadas), base SINTETICA con el esquema real; el resumen
nunca debe incluir PnL ni resultados de sombras."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.trading.measurement_status import MeasurementStatus
from scripts import check_health, telegram_daily
from scripts.telegram_daily import (
    DbSnapshot,
    build_message,
    collect_snapshot,
    read_snapshot,
    run,
    snapshot_database,
)
from tests.unit.synthetic_measurement import (
    BASE,
    SYNTHETIC_HASH,
    Group,
    create_schema,
    insert_groups,
    open_read_only,
    scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "telegram_daily.py"
NOW = BASE + timedelta(hours=100)
HUGE_RETURN = 987654.0  # un PnL inconfundible: no debe aparecer jamas en el resumen

HEALTH_OK = {
    "status": "ok", "mode": "PAPER_TRADING",
    "last_beat_at": (NOW - timedelta(seconds=20)).isoformat(),
    "last_cycle_at": (NOW - timedelta(minutes=3)).isoformat(),
    "log_dropped_count": 0,
    "llm_labeling": {
        "enabled": True, "frozen_hash_ok": True, "labeled_today": 5, "failed_today": 0,
        "budget_exceeded_today": 0, "budget_exhausted_at": None, "cost_today_usd": 0.01,
    },
}


def _health(**overrides):
    out = {**HEALTH_OK, "llm_labeling": dict(HEALTH_OK["llm_labeling"])}
    for key, value in overrides.items():
        if key in out["llm_labeling"]:
            out["llm_labeling"][key] = value
        else:
            out[key] = value
    return out


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_daily.db"
    await create_schema(path)
    groups = scenario(10, 10, approved_ret=HUGE_RETURN, rejected_ret=-HUGE_RETURN)
    # 24 h previas a NOW = BASE+100h: ventana (BASE+76h, BASE+100h]
    window_failures = [
        Group(key="f-timeout", opened_at=BASE + timedelta(hours=95), label="SIN_LLM",
              ret=None, status="TIMEOUT"),
        Group(key="f-invalid", opened_at=BASE + timedelta(hours=96), label="SIN_LLM",
              ret=None, status="INVALID"),
        Group(key="f-budget", opened_at=BASE + timedelta(hours=97), label="SIN_LLM",
              ret=None, status="BUDGET_EXCEEDED"),
        Group(key="old-timeout", opened_at=BASE + timedelta(hours=10), label="SIN_LLM",
              ret=None, status="TIMEOUT"),
        Group(key="pilot-in-window", opened_at=BASE + timedelta(hours=98), fase="PILOTO",
              closed_at=BASE + timedelta(hours=99), ret=1.0),
        Group(key="other-hash", opened_at=BASE + timedelta(hours=99), prompt_hash="otro",
              closed_at=BASE + timedelta(hours=99, minutes=30), ret=1.0),
    ]
    insert_groups(path, groups + window_failures)
    return path


def snapshot_of(path, now=NOW) -> DbSnapshot:
    conn = open_read_only(path)
    try:
        return read_snapshot(conn, now, SYNTHETIC_HASH)
    finally:
        conn.close()


# --- copia de solo lectura --------------------------------------------------------


async def test_snapshot_database_copies_consistently_and_never_touches_the_source(
    db_path, tmp_path,
):
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    dest_dir = tmp_path / "copia"
    dest_dir.mkdir()

    copy = snapshot_database(str(db_path), dest_dir)

    assert copy.parent == dest_dir.resolve() and copy != db_path.resolve()
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    src, dst = sqlite3.connect(db_path), sqlite3.connect(copy)
    try:
        query = "SELECT COUNT(*) FROM llm_logs"
        assert src.execute(query).fetchone() == dst.execute(query).fetchone()
    finally:
        src.close()
        dst.close()


def test_snapshot_database_refuses_to_write_inside_data():
    inside_data = Path("data") / "no_debe_crearse"

    with pytest.raises(AssertionError, match="data/"):
        snapshot_database("cualquiera.db", inside_data)

    assert not inside_data.exists()


async def test_collect_snapshot_deletes_its_temporary_copy(db_path, monkeypatch):
    seen: list[Path] = []
    real = telegram_daily.snapshot_database

    def spy(source, dest_dir):
        copy = real(source, dest_dir)
        seen.append(copy)
        return copy

    monkeypatch.setattr(telegram_daily, "snapshot_database", spy)

    snap = collect_snapshot(str(db_path), NOW, SYNTHETIC_HASH)

    assert snap.approved_24h == 2 and len(seen) == 1
    assert not seen[0].exists()
    assert Path("data").resolve() not in seen[0].parents


# --- lectura: 24 h y solo lo permitido --------------------------------------------


async def test_read_snapshot_counts_the_last_24_hours_of_the_measurement(db_path):
    snap = snapshot_of(db_path)

    assert (snap.approved_24h, snap.rejected_24h) == (2, 2)
    assert snap.failed_by_cause_24h == {"TIMEOUT": 1, "INVALID": 1}  # el TIMEOUT viejo no
    assert snap.budget_exceeded_24h == 1
    # costo de TODAS las filas de la ventana (cualquier fase/hash): 2+2+3 + piloto + otro hash
    assert snap.cost_24h_usd == pytest.approx(0.009)
    # acumulado de la medicion bajo el hash: 10+10 + 3 fallos + 1 TIMEOUT viejo = 24 filas
    assert snap.status.total_medicion_rows == 24
    assert snap.status.total_cost_usd == pytest.approx(0.024)
    assert (snap.status.n_eff_approved, snap.status.n_eff_rejected) == (10, 10)


async def test_read_snapshot_never_queries_pnl_or_trade_results(db_path):
    statements: list[str] = []
    conn = open_read_only(db_path)
    conn.set_trace_callback(statements.append)
    try:
        read_snapshot(conn, NOW, SYNTHETIC_HASH)
    finally:
        conn.close()

    assert statements
    for sql in statements:
        lowered = sql.lower()
        for forbidden in ("pnl", "exit_price", "close_reason", "fee_", "funding_paid"):
            assert forbidden not in lowered, sql


# --- mensaje ----------------------------------------------------------------------


async def test_message_has_health_24h_cost_effective_n_and_gate_and_no_pnl(db_path):
    msg = build_message(
        health=_health(), health_error=None, snapshot=snapshot_of(db_path), db_error=None,
        now=NOW, tz_name="America/Santiago",
    )

    # BASE + 100 h = 2026-10-05 04:00 UTC = 01:00 en Chile (UTC-3, horario de verano)
    assert "resumen diario 2026-10-05 01:00 (America/Santiago)" in msg
    assert "MODO PAPER TRADING" in msg
    assert "Bot (puerto 8000): OK | hash congelado OK: si | etiquetado en vivo: activo" in msg
    assert "Ultimo ciclo de senales: hace 3 min" in msg
    assert "- Etiquetadas: 4 (APROBADA 2 / RECHAZADA 2)" in msg
    assert "- Fallidas: 2 (INVALID 1, TIMEOUT 1)" in msg
    assert "- Sin presupuesto (BUDGET_EXCEEDED): 1" in msg
    assert "Costo del LLM en 24 h: 0.0090 USD" in msg
    assert "Costo acumulado de la medicion: 0.0240 USD en 24 filas" in msg
    assert "- N efectivo APROBADA: 10/300" in msg and "- N efectivo RECHAZADA: 10/300" in msg
    assert "Gate: CERRADO (faltan 290 aprobadas y 290 rechazadas)" in msg
    assert "ATENCION" not in msg
    for forbidden in (str(int(HUGE_RETURN)), "4938270", "pnl", "PnL neto", "exit_price"):
        assert forbidden not in msg
    # la unica mencion a PnL es la aclaracion de que NO se incluye
    assert msg.count("PnL") == 1 and "no incluye resultados (PnL)" in msg


def test_message_says_the_gate_is_open_and_points_to_the_procedure():
    open_status = MeasurementStatus(frozen_hash="h", n_eff_approved=300, n_eff_rejected=312)
    snap = DbSnapshot(open_status, 1, 1, {}, 0, 0.0)

    msg = build_message(
        health=_health(), health_error=None, snapshot=snap, db_error=None, now=NOW,
    )

    assert "Gate: ABIERTO" in msg and "docs/VEREDICTO_3_6.md" in msg
    assert "placebo oficial" in msg


def test_message_says_so_when_the_bot_does_not_respond_and_still_reports_the_database():
    snap = DbSnapshot(MeasurementStatus(frozen_hash="h"), 0, 0, {}, 0, 0.0)

    msg = build_message(
        health=None, health_error="http://127.0.0.1:8000/health: connection refused",
        snapshot=snap, db_error=None, now=NOW,
    )

    assert "BOT SIN RESPUESTA (puerto 8000)" in msg and "connection refused" in msg
    assert "sale de la base" in msg
    assert "Gate: CERRADO" in msg


def test_message_flags_stale_unfrozen_and_disabled_labeling():
    msg = build_message(
        health=_health(status="stale", frozen_hash_ok=False, enabled=False),
        health_error=None, snapshot=None, db_error="OperationalError: unable to open",
        now=NOW,
    )

    assert "Bot (puerto 8000): STALE | hash congelado OK: NO" in msg
    assert "etiquetado en vivo: DESACTIVADO" in msg
    assert "ATENCION: el bot esta STALE" in msg
    assert "NO coincide con el congelado" in msg
    assert "etiquetado en vivo esta desactivado" in msg
    assert "Base: no se pudo leer (OperationalError: unable to open)" in msg


def test_message_flags_a_deliberate_pause_and_the_kill_switch():
    msg = build_message(
        health=_health(control={"paused": True, "kill_switch_active": True}),
        health_error=None, snapshot=None, db_error=None, now=NOW,
    )

    assert "scheduler esta PAUSADO a proposito" in msg and "medicion esta DETENIDA" in msg
    assert "KILL SWITCH ACTIVO" in msg
    quiet = build_message(
        health=_health(control={"paused": False, "kill_switch_active": False}),
        health_error=None, snapshot=None, db_error=None, now=NOW,
    )
    assert "PAUSADO" not in quiet and "KILL SWITCH" not in quiet


def test_message_without_any_cycle_yet():
    msg = build_message(
        health=_health(last_cycle_at=None), health_error=None, snapshot=None, db_error=None,
        now=NOW,
    )

    assert "Ultimo ciclo de senales: ninguno todavia" in msg


# --- run(): envio -----------------------------------------------------------------


class Sender:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple] = []

    def __call__(self, token, chat_id, text, *, timeout=10.0) -> bool:
        self.calls.append((token, chat_id, text))
        return self.ok


def _run(sender, *, fetch=None, snapshot=None, dry_run=False, **kw):
    fetch = fetch or (lambda host, port, timeout: (HEALTH_OK, None))
    snapshot = snapshot or (
        lambda path, now, frozen: DbSnapshot(MeasurementStatus(frozen_hash="h"), 0, 0, {}, 0, 0.0)
    )
    return run(
        host="127.0.0.1", port=8000, timeout=1.0, db_path="x.db", token="TOKEN-SECRETO",
        chat_id="CHAT-SECRETO", dry_run=dry_run, now=NOW, fetch_health_fn=fetch,
        snapshot_fn=snapshot, send_fn=sender, **kw,
    )


def test_run_sends_exactly_one_message_and_never_leaks_the_credentials(capsys):
    sender = Sender()

    assert _run(sender) is True

    assert len(sender.calls) == 1
    token, chat_id, text = sender.calls[0]
    assert (token, chat_id) == ("TOKEN-SECRETO", "CHAT-SECRETO")
    assert "TOKEN-SECRETO" not in text and "CHAT-SECRETO" not in text
    out = capsys.readouterr().out
    assert "TOKEN-SECRETO" not in out and "CHAT-SECRETO" not in out


def test_run_returns_false_when_telegram_fails():
    assert _run(Sender(ok=False)) is False


def test_run_dry_run_prints_without_sending(capsys):
    sender = Sender()

    assert _run(sender, dry_run=True) is True

    assert sender.calls == []
    assert "Minerva - resumen diario" in capsys.readouterr().out


def test_run_with_the_bot_down_still_sends_a_summary_that_says_so():
    sender = Sender()

    ok = _run(sender, fetch=lambda host, port, timeout: (None, "refused"))

    assert ok is True and len(sender.calls) == 1
    assert "BOT SIN RESPUESTA" in sender.calls[0][2]


def test_run_with_an_unreadable_database_still_sends_and_reports_it():
    sender = Sender()

    def broken(path, now, frozen):
        raise sqlite3.OperationalError("unable to open database file")

    assert _run(sender, snapshot=broken) is True

    assert "Base: no se pudo leer (OperationalError: unable to open database file)" in (
        sender.calls[0][2]
    )


# --- fetch_health -----------------------------------------------------------------


def test_fetch_health_returns_the_json_or_the_error(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok"}

    monkeypatch.setattr(check_health.httpx, "get", lambda url, timeout: _Response())
    assert check_health.fetch_health("h", 1, 1.0) == ({"status": "ok"}, None)

    def boom(url, timeout):
        raise RuntimeError("sin red")

    monkeypatch.setattr(check_health.httpx, "get", boom)
    data, error = check_health.fetch_health("h", 1, 1.0)
    assert data is None and error == "http://h:1/health: sin red"


# --- CLI como subproceso (cwd ajeno, como el Programador de tareas) ---------------


def _cli(cwd, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )


async def test_cli_dry_run_from_an_unrelated_cwd_reports_a_down_bot(db_path, tmp_path):
    proc = _cli(
        tmp_path, "--dry-run", "--port", "9", "--timeout", "2", "--db", str(db_path),
    )

    assert proc.returncode == 0, proc.stderr
    assert "Minerva - resumen diario" in proc.stdout
    assert "BOT SIN RESPUESTA (puerto 9)" in proc.stdout
    # hash real de la medicion: la base sintetica no tiene filas bajo el: 0/300
    assert "- N efectivo APROBADA: 0/300" in proc.stdout
    assert "Gate: CERRADO" in proc.stdout


def test_cli_dry_run_with_a_missing_database_reports_it_and_does_not_crash(tmp_path):
    proc = _cli(
        tmp_path, "--dry-run", "--port", "9", "--timeout", "2",
        "--db", str(tmp_path / "no_existe.db"),
    )

    assert proc.returncode == 0, proc.stderr
    assert "Base: no se pudo leer" in proc.stdout
    assert "BOT SIN RESPUESTA" in proc.stdout


def test_default_database_is_the_one_start_bot_uses_not_the_env_one():
    start_bot = (REPO_ROOT / "scripts" / "start_bot.ps1").read_text(encoding="utf-8")

    assert f'$DatabasePath = "{telegram_daily.DEFAULT_DB_PATH}"' in start_bot


def test_summary_window_constants_are_24_hours():
    assert telegram_daily.SUMMARY_WINDOW_HOURS == 24
    assert isinstance(NOW, datetime) and NOW.tzinfo is UTC
