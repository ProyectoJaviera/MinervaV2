"""Fase 5, base: interruptores (el push de Telegram solo lo activa `start_bot.ps1` con el puerto
8000; los controles del dashboard se pueden apagar), tabla `control_audit` aditiva e idempotente,
puerta de resultados que falla cerrada y acciones de control compartidas entre Telegram y el
dashboard (misma semantica, mismo texto de advertencia, rastro de quien y cuando)."""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.persistence.database import Database
from app.persistence.repositories import control_audit_repo as audit
from app.persistence.repositories import system_state_repo
from app.telegram import commands as telegram_commands
from app.telegram.commands import CommandRouter
from app.telegram.confirmation import ConfirmationManager
from app.trading import control_actions, control_state, reveal, risk_engine
from app.trading.control_actions import (
    ACTIONS,
    CHANNEL_DASHBOARD,
    CONFIRM_PHRASES,
    DASHBOARD_WARNINGS,
    KILL,
    PAUSE,
    RESUME,
    STOPS_MEASUREMENT,
    apply_control,
    read_state,
)
from tests.unit.test_telegram_commands import make_ctx

REPO_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


# --- interruptores -------------------------------------------------------------------------------


def test_the_push_is_off_by_default_and_the_controls_are_on():
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.telegram_push_enabled is False
    assert settings.telegram_push_poll_seconds == 10.0
    assert settings.dashboard_controls_enabled is True


def test_the_switches_follow_their_env_vars_and_are_independent():
    push = Settings(_env_file=None, TELEGRAM_PUSH_ENABLED=True)  # type: ignore[call-arg]
    off = Settings(_env_file=None, DASHBOARD_CONTROLS_ENABLED=False)  # type: ignore[call-arg]

    assert push.telegram_push_enabled and not push.telegram_commands_enabled
    assert off.dashboard_controls_enabled is False and off.telegram_push_enabled is False


def test_start_bot_activates_the_push_only_for_port_8000():
    text = (REPO_ROOT / "scripts" / "start_bot.ps1").read_text(encoding="utf-8")

    assert '$isRealBot = ($Port -eq 8000)' in text
    assert '$telegramPush = if ($isRealBot) { "true" } else { "false" }' in text
    assert "$env:TELEGRAM_PUSH_ENABLED = $telegramPush" in text
    assert "TELEGRAM_PUSH_ENABLED=$telegramPush" in text  # queda en el diario operativo
    assert 'TELEGRAM_PUSH_ENABLED = "true"' not in text  # nunca sin pasar por la condicion


def test_the_dev_server_and_the_env_example_keep_the_push_off():
    dev = (REPO_ROOT / "scripts" / "run_dev_server.py").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()

    assert 'os.environ["TELEGRAM_PUSH_ENABLED"] = "false"' in dev
    assert "TELEGRAM_PUSH_ENABLED=false" in example
    assert "DASHBOARD_CONTROLS_ENABLED=true" in example


# --- tabla control_audit -------------------------------------------------------------------------


def _tables(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        names = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}
    finally:
        conn.close()


async def test_a_database_from_before_phase_5_gets_only_the_audit_table(tmp_path):
    path = tmp_path / "old_shape.db"
    database = Database(str(path))
    await database.connect()
    await database.close()
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE control_audit")
    conn.commit()
    conn.close()
    before = _tables(path)
    assert "control_audit" not in before

    for _ in range(2):  # un segundo arranque debe ser inocuo
        reopened = Database(str(path))
        await reopened.connect()
        await reopened.close()

    after = _tables(path)
    assert set(after) - set(before) == {"control_audit"}
    assert after.pop("control_audit") == 0
    assert after == before


REAL_DB = REPO_ROOT / "data" / "minerva_estabilidad.db"


@pytest.mark.skipif(not REAL_DB.exists(), reason="no hay copia local de la base real")
async def test_the_migration_on_a_copy_of_the_real_database_changes_nothing_else(tmp_path):
    """Smoke sobre datos reales: la base real se abre en SOLO LECTURA y se copia con la API de
    backup; solo la copia se migra (nunca la de `data/`)."""
    copy = tmp_path / "real_copy.db"
    assert copy.resolve() != REAL_DB.resolve()
    source = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(str(copy))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    before = _tables(copy)

    for _ in range(2):
        database = Database(str(copy))
        await database.connect()
        await database.close()

    after = _tables(copy)
    created = set(after) - set(before)
    assert created <= {"control_audit"}
    assert all(after[t] == 0 for t in created)
    assert {k: v for k, v in after.items() if k in before} == before
    conn = sqlite3.connect(str(copy))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


async def test_the_audit_table_has_no_result_nor_chat_columns(db):
    rows = await db.fetch_all("PRAGMA table_info(control_audit)")
    columns = {r["name"] for r in rows}

    assert columns == {
        "id", "created_at", "channel", "actor", "action", "paused_before", "kill_before",
        "paused_after", "kill_after",
    }
    assert not any("pnl" in c or "chat" in c for c in columns)


async def test_the_audit_table_only_accepts_known_channels_and_actions(db):
    insert = (
        "INSERT INTO control_audit (created_at, channel, actor, action, paused_before, "
        "kill_before, paused_after, kill_after) VALUES ('x', ?, 'a', ?, 0, 0, 1, 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(insert, ("WEB", "pause"))
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(insert, ("DASHBOARD", "borrar"))
    await db.execute(insert, ("DASHBOARD", "pause"))  # los validos pasan


async def test_the_audit_repo_stores_who_when_and_before_after(db):
    first = await audit.insert(
        db, channel="DASHBOARD", actor="renzo", action="kill", paused_before=False,
        kill_before=False, paused_after=True, kill_after=True, created_at=T0,
    )
    second = await audit.insert(
        db, channel="TELEGRAM", actor="telegram", action="pause", paused_before=True,
        kill_before=True, paused_after=True, kill_after=True,
        created_at=T0.astimezone(timezone(timedelta(hours=-3))) + timedelta(hours=1),
    )

    recent = await audit.get_recent(db, limit=5)

    assert [e.id for e in recent] == [second, first]  # la mas nueva primero
    assert recent[1].actor == "renzo" and recent[1].created_at == T0
    assert recent[1].changed is True and recent[0].changed is False  # pausa sobre pausa: nada
    assert recent[0].created_at == T0 + timedelta(hours=1)  # se guarda en UTC aunque llegue en -03
    assert (await audit.get_latest(db)).id == second


# --- la puerta falla cerrada ---------------------------------------------------------------------


async def test_the_gate_is_closed_without_a_measurement_and_fails_closed_on_errors(
    db, monkeypatch, caplog,
):
    assert await reveal.reveal_allowed(db) is False  # base vacia: gate cerrado

    async def broken(_db):
        raise RuntimeError("base ilegible con clave=SECRETO")

    monkeypatch.setattr(reveal, "compute_status_async", broken)
    monkeypatch.setattr(reveal, "is_outcome_reveal_allowed", lambda status: True)
    with caplog.at_level("ERROR", logger="app.trading.reveal"):
        assert await reveal.reveal_allowed(db) is False  # aunque la puerta diria "abierta"

    assert any("se mantienen ocultos" in r.getMessage() for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


async def test_the_gate_opens_only_when_the_real_condition_says_so(db, monkeypatch):
    seen = {}

    async def fake_status(_db):
        return "estado"

    def fake_allowed(status):
        seen["status"] = status
        return True

    monkeypatch.setattr(reveal, "compute_status_async", fake_status)
    monkeypatch.setattr(reveal, "is_outcome_reveal_allowed", fake_allowed)

    assert await reveal.reveal_allowed(db) is True
    assert seen == {"status": "estado"}


def test_every_copy_of_the_hidden_results_text_is_the_same():
    from app.memory import gate

    assert reveal.HIDDEN_RESULTS == telegram_commands.HIDDEN_RESULTS == gate.HIDDEN_RESULTS


# --- acciones de control compartidas -------------------------------------------------------------


async def state_of(db):
    return await read_state(db)


async def test_pause_pauses_without_the_kill_switch(db):
    result = await apply_control(db, PAUSE, channel=CHANNEL_DASHBOARD, actor="renzo", now=T0)

    assert (result.before.paused, result.before.kill_switch) == (False, False)
    assert (result.after.paused, result.after.kill_switch) == (True, False)
    assert result.changed is True
    assert await control_state.is_scheduler_paused(db) is True
    assert await risk_engine.is_kill_switch_active(db) is False


async def test_kill_activates_the_kill_switch_and_pauses(db):
    result = await apply_control(db, KILL, channel=CHANNEL_DASHBOARD, actor="renzo", now=T0)

    assert (result.after.paused, result.after.kill_switch) == (True, True)
    assert await control_state.is_scheduler_paused(db) is True
    assert await risk_engine.is_kill_switch_active(db) is True


async def test_resume_lifts_both_and_warns_about_the_drawdown_stop(db):
    await apply_control(db, KILL, channel=CHANNEL_DASHBOARD, actor="renzo")
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")

    result = await apply_control(db, RESUME, channel=CHANNEL_DASHBOARD, actor="renzo")

    assert (result.after.paused, result.after.kill_switch) == (False, False)
    assert result.drawdown_stop_active is True  # no se levanta aqui
    assert await risk_engine.is_drawdown_stop_active(db) is True


async def test_every_valid_request_is_audited_even_when_nothing_changes(db):
    await apply_control(db, PAUSE, channel=CHANNEL_DASHBOARD, actor="renzo", now=T0)
    again = await apply_control(db, PAUSE, channel=CHANNEL_DASHBOARD, actor="renzo", now=T0)

    rows = await audit.get_recent(db)

    assert again.changed is False and len(rows) == 2
    assert [r.changed for r in rows] == [False, True]
    assert all(r.channel == "DASHBOARD" and r.actor == "renzo" for r in rows)


async def test_unknown_actions_channels_and_blank_actors_are_handled(db):
    with pytest.raises(ValueError):
        await apply_control(db, "borrar", channel=CHANNEL_DASHBOARD, actor="renzo")
    with pytest.raises(ValueError):
        await apply_control(db, PAUSE, channel="WEB", actor="renzo")
    assert await state_of(db) == control_actions.ControlState(paused=False, kill_switch=False)
    assert await audit.get_recent(db) == []  # nada se registra si se rechazo

    await apply_control(db, PAUSE, channel=CHANNEL_DASHBOARD, actor="   ")
    await apply_control(db, RESUME, channel=CHANNEL_DASHBOARD, actor="x" * 500)
    rows = await audit.get_recent(db)
    assert [r.actor for r in rows] == ["x" * 64, "desconocido"]


async def test_concurrent_actions_are_serialized_and_the_audit_chain_is_coherent(db):
    actions = [PAUSE, KILL, RESUME, PAUSE, RESUME, KILL, RESUME] * 4

    await asyncio.gather(*(
        apply_control(db, a, channel=CHANNEL_DASHBOARD, actor=f"u{i}")
        for i, a in enumerate(actions)
    ))

    rows = sorted(await audit.get_recent(db, limit=100), key=lambda r: r.id)
    assert len(rows) == len(actions)
    for earlier, later in zip(rows, rows[1:], strict=False):
        assert (later.paused_before, later.kill_before) == (
            earlier.paused_after, earlier.kill_after)
    final = await state_of(db)
    assert (final.paused, final.kill_switch) == (rows[-1].paused_after, rows[-1].kill_after)


def test_the_phrases_are_distinct_and_every_action_has_a_warning():
    assert set(CONFIRM_PHRASES) == set(ACTIONS) == set(DASHBOARD_WARNINGS)
    assert len(set(CONFIRM_PHRASES.values())) == 3
    assert all(phrase == phrase.upper() and phrase.isalpha() for phrase in CONFIRM_PHRASES.values())


def test_the_dashboard_and_telegram_share_the_same_measurement_warning():
    for action in (PAUSE, KILL):
        assert STOPS_MEASUREMENT in DASHBOARD_WARNINGS[action]
    for command in ("/pause", "/kill"):
        assert STOPS_MEASUREMENT in telegram_commands.CONTROL_WARNINGS[command]
    assert control_actions.RESUME_LIFTS in DASHBOARD_WARNINGS[RESUME]
    assert control_actions.RESUME_LIFTS in telegram_commands.CONTROL_WARNINGS["/resume"]
    assert "DETIENE LA MEDICION" in STOPS_MEASUREMENT


# --- Telegram usa la misma logica y deja el mismo rastro -----------------------------------------


class Clock:
    t = 1000.0

    def __call__(self) -> float:
        return self.t


async def test_telegram_controls_go_through_the_shared_logic_and_are_audited(db):
    ctx, _ = make_ctx(db)
    router = CommandRouter(ctx, ConfirmationManager(now=Clock()))

    await router("/kill")
    reply = await router("/kill SI")
    await router("/resume")
    await router("/resume SI")

    assert "KILL SWITCH ACTIVO" in reply
    rows = sorted(await audit.get_recent(db), key=lambda r: r.id)
    assert [(r.action, r.channel, r.actor) for r in rows] == [
        ("kill", "TELEGRAM", "telegram"), ("resume", "TELEGRAM", "telegram"),
    ]
    assert (rows[0].paused_after, rows[0].kill_after) == (True, True)
    assert (rows[1].paused_after, rows[1].kill_after) == (False, False)
    # sin datos del chat, y el rastro de siempre sigue escribiendose
    assert not any("777" in r.actor for r in rows)
    assert await system_state_repo.get_state(db, "telegram_last_control_action")


async def test_a_telegram_request_without_confirmation_leaves_no_audit_row(db):
    ctx, _ = make_ctx(db)
    router = CommandRouter(ctx, ConfirmationManager(now=Clock()))

    await router("/pause")
    await router("/pause SI SI")

    assert await audit.get_recent(db) == []
    assert await control_state.is_scheduler_paused(db) is False


# --- las lecturas nuevas del motor de riesgo -----------------------------------------------------


async def test_the_new_risk_reads_default_to_safe_values(db):
    assert await risk_engine.is_daily_loss_limit_locked(db) is False
    assert await risk_engine.get_consecutive_losses(db) == 0

    await system_state_repo.set_state(db, "daily_loss_limit_locked", "1")
    await system_state_repo.set_state(db, "consecutive_losses", "3")

    assert await risk_engine.is_daily_loss_limit_locked(db) is True
    assert await risk_engine.get_consecutive_losses(db) == 3


# --- el modulo compartido no conoce el LLM ni el nucleo de la medicion ---------------------------


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


@pytest.mark.parametrize("relative", [
    "app/trading/control_actions.py", "app/trading/reveal.py",
    "app/persistence/repositories/control_audit_repo.py",
])
def test_the_shared_modules_do_not_touch_the_llm_the_labeler_or_the_arm(relative):
    forbidden = (
        "anthropic", "app.llm", "app.core.scheduler", "app.trading.shadow_live_labeler",
        "app.trading.shadow_book", "app.trading.paper_execution", "app.trading.position_monitor",
        "app.execution", "app.news", "app.memory",
    )
    imported = _imports(REPO_ROOT / relative)

    offenders = [m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)]
    assert offenders == []
