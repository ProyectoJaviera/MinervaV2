"""Fase 6: `scripts/resume_drawdown_stop.py`, la accion manual que los avisos prometian ("se levanta
aparte (resume_drawdown_stop)") y que no tenia como ejecutarse. Por defecto no escribe; exige
`--confirm`; solo actua si el stop esta activo; no toca el contador del criterio de paso a dinero
real; reinicia el pico para que una caida NUEVA pueda volver a activarlo; y nunca imprime saldo ni
equity mientras los resultados estan ocultos."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.persistence.database import Database
from app.persistence.models import Side, Trade
from app.persistence.repositories import system_state_repo, trades_repo
from app.trading import risk_engine
from scripts import resume_drawdown_stop as script
from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = Settings(  # type: ignore[call-arg]
    _env_file=None, INITIAL_CAPITAL_USDT=100.0, MAX_DRAWDOWN_PCT=0.2,
)


async def make_db(path: Path, *, active: bool = True, losses: tuple[float, ...] = (-15.0, -9.5)):
    """Una base con el stop `active`, el pico en 100, un contador de 3 y perdidas realizadas."""
    db = Database(str(path))
    await db.connect()
    try:
        await system_state_repo.set_state(db, "drawdown_stop_active", "1" if active else "0")
        await system_state_repo.set_state(db, "drawdown_breach_in_progress", "1")
        await system_state_repo.set_state(db, "equity_peak_usdt", "100.0")
        await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "3")
        for pnl in losses:
            trade = await trades_repo.create_trade(db, Trade(
                symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21", leverage=10,
                margin_usdt=5.0, notional_usdt=50.0, qty=0.5, entry_price=100.0,
                opened_at=datetime.now(UTC),
            ))
            await trades_repo.close_trade(
                db, trade.id, exit_price=95.0, fee_exit_usdt=0.0, pnl_gross_usdt=pnl,
                pnl_net_usdt=pnl, close_reason="SL", closed_at=datetime.now(UTC),
            )
    finally:
        await db.close()


async def state(path: Path) -> dict[str, str | None]:
    db = Database(str(path))
    await db.connect()
    try:
        return {
            key: await system_state_repo.get_state(db, key) for key in (
                "drawdown_stop_active", "drawdown_breach_in_progress", "equity_peak_usdt",
                "drawdown_stop_would_have_triggered_count", "drawdown_stop_last_resumed_at",
            )
        }
    finally:
        await db.close()


async def test_without_confirm_it_only_looks_and_writes_nothing(tmp_path):
    await make_db(tmp_path / "bot.db")
    before = hashlib.sha256((tmp_path / "bot.db").read_bytes()).hexdigest()

    code, message = await script.run(
        str(tmp_path / "bot.db"), confirm=False, initial_capital=100.0,
    )

    assert code == script.EXIT_OK and "Stop por drawdown: ACTIVO" in message
    assert "--confirm" in message and "Sin cambios" in message
    assert hashlib.sha256((tmp_path / "bot.db").read_bytes()).hexdigest() == before
    assert (await state(tmp_path / "bot.db"))["drawdown_stop_active"] == "1"


async def test_confirm_lifts_the_stop_resets_the_peak_and_keeps_the_real_money_counter(tmp_path):
    await make_db(tmp_path / "bot.db")  # perdidas realizadas -24.5 sobre 100 -> equity 75.5

    code, message = await script.run(str(tmp_path / "bot.db"), confirm=True, initial_capital=100.0)

    assert code == script.EXIT_OK, message
    after = await state(tmp_path / "bot.db")
    assert after["drawdown_stop_active"] == "0" and after["drawdown_breach_in_progress"] == "0"
    # capital inicial + PnL realizado
    assert float(after["equity_peak_usdt"]) == pytest.approx(75.5)
    assert after["drawdown_stop_would_have_triggered_count"] == "3"  # NO se reinicia
    assert after["drawdown_stop_last_resumed_at"] is not None  # queda el rastro
    assert "LEVANTADO" in message and "NO se toco (valor 3)" in message


async def test_it_never_prints_balance_or_equity_while_the_results_are_hidden(tmp_path):
    await make_db(tmp_path / "bot.db")

    _code, message = await script.run(
        str(tmp_path / "bot.db"), confirm=True, equity=77.1234, initial_capital=100.0,
    )

    for leaked in ("75.5", "77.1234", "77.12", "24.5"):
        assert leaked not in message
    assert "valor oculto" in message


async def test_an_explicit_equity_becomes_the_new_peak(tmp_path):
    await make_db(tmp_path / "bot.db")

    code, _ = await script.run(str(tmp_path / "bot.db"), confirm=True, equity=82.25)

    assert code == 0
    assert float((await state(tmp_path / "bot.db"))["equity_peak_usdt"]) == pytest.approx(82.25)


async def test_confirm_does_nothing_when_the_stop_is_not_active(tmp_path):
    await make_db(tmp_path / "bot.db", active=False)
    before = hashlib.sha256((tmp_path / "bot.db").read_bytes()).hexdigest()

    code, message = await script.run(str(tmp_path / "bot.db"), confirm=True)

    assert code == script.EXIT_NOT_ACTIVE and "nada que levantar" in message
    assert hashlib.sha256((tmp_path / "bot.db").read_bytes()).hexdigest() == before


async def test_a_missing_database_is_reported_without_creating_it(tmp_path):
    code, message = await script.run(str(tmp_path / "no_existe.db"), confirm=True)

    assert code == script.EXIT_NO_DB and "No existe la base" in message
    assert not (tmp_path / "no_existe.db").exists()


async def test_after_lifting_a_new_drop_can_trigger_the_stop_again(tmp_path):
    # El bug historico: sin reiniciar el pico, el stop no volvia a activarse nunca mas.
    await make_db(tmp_path / "bot.db")
    await script.run(str(tmp_path / "bot.db"), confirm=True, equity=75.5)
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    try:
        await risk_engine.update_equity_tracking(db, SETTINGS, 74.0)  # caida chica: no activa
        assert await risk_engine.is_drawdown_stop_active(db) is False
        await risk_engine.update_equity_tracking(db, SETTINGS, 60.0)  # 20.5 % bajo el pico nuevo
        assert await risk_engine.is_drawdown_stop_active(db) is True
        assert await risk_engine.get_drawdown_stop_trigger_count(db) == 4  # el contador siguio
    finally:
        await db.close()


def test_the_cli_rejects_a_non_positive_equity():
    with pytest.raises(SystemExit) as exc:
        script.main(["--equity", "0"])

    assert exc.value.code == 2


def test_the_status_mode_runs_standalone_and_exits_zero(tmp_path):
    asyncio.run(make_db(tmp_path / "bot.db"))

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "resume_drawdown_stop.py"),
         "--db", str(tmp_path / "bot.db")],
        capture_output=True, text=True, timeout=120, cwd=REPO_ROOT, creationflags=NO_WINDOW,
    )

    assert result.returncode == 0, result.stderr
    assert "Stop por drawdown: ACTIVO" in result.stdout
