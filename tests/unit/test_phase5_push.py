"""Fase 5, notificaciones push de Telegram: sondeo de la base sin ganchos en el nucleo, linea base
sin inundar el chat, cada evento (posicion abierta/cerrada, universo, alertas de riesgo), cierre sin
resultados mientras la puerta este cerrada, entrega con ritmo/reintentos/`retry_after`, estado
persistido sin repetir ni perder mensajes y aislamiento frente a un Telegram hostil. Sin red: un
cliente falso; ninguna prueba escribe a un chat real."""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.market.attribution import COINGECKO_TEXT, COINGECKO_URL
from app.persistence.models import AssetUniverseEntry, Side, Trade, TradeStatus
from app.persistence.repositories import system_state_repo, trades_repo, universe_repo
from app.telegram import push, push_runtime
from app.telegram.client import TelegramApiError
from app.telegram.push import PushNotifier
from app.trading import control_actions
from app.trading.position_monitor import PositionMonitor
from app.trading.reveal import HIDDEN_RESULTS
from tests.unit.lifespan_fakes import patch_lifespan
from tests.unit.test_news_runtime_isolation import CountingLabeler
from tests.unit.test_position_monitor import FakeRest, _open_long, _ws_ticker
from tests.unit.test_position_monitor import make_settings as monitor_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456:ABC-TOKEN-SECRETO"
CHAT_ID = "777000111"
H = timedelta(hours=1)
SENTINELS = (
    "123.456789", "124.567891", "987.654321", "SENTINEL_REASON", "0.013579", "0.035791",
    "SENTINEL_FILL",
)


def now() -> datetime:
    return datetime.now(UTC)


def make_settings(**overrides) -> Settings:
    defaults = {
        "TELEGRAM_PUSH_ENABLED": True, "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT_ID,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


class FakeClient:
    """`script`: una entrada por llamada (una excepcion se lanza, `None` envia bien)."""

    def __init__(self, script=None) -> None:
        self.script = list(script or [])
        self.sent: list[str] = []
        self.calls = 0
        self.closed = False

    async def send_message(self, chat_id, text) -> None:
        self.calls += 1
        assert chat_id == CHAT_ID
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
        self.sent.append(text)

    async def aclose(self) -> None:
        self.closed = True


class Clock:
    """Reloj monotonico falso: `sleep` lo adelanta, asi el ritmo se prueba sin esperar."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_notifier(db, client=None, *, settings=None, **kwargs):
    clock = Clock()
    notifier = PushNotifier(
        db, settings or make_settings(), client or FakeClient(), sleep=clock.sleep,
        monotonic=clock, **kwargs,
    )
    return notifier, clock


class Gate:
    open = False
    calls = 0


@pytest.fixture
def gate(monkeypatch):
    state = Gate()

    async def fake(_db):
        state.calls += 1
        return state.open

    monkeypatch.setattr(push, "reveal_allowed", fake)
    return state


async def new_trade(db, symbol="BTCUSDT", side=Side.LONG, **kw) -> Trade:
    values = dict(
        symbol=symbol, side=side, strategy="ema_cross_9_21", leverage=10, margin_usdt=5.0,
        notional_usdt=50.0, qty=0.0007, entry_price=64000.5, opened_at=now() - 2 * H,
        sl_price=63100.0, tp_price=66000.0, liq_price=58000.0, sl_margin_loss_pct=14.0,
        decision_source="LLM_APROBADA",
    )
    values.update(kw)
    return await trades_repo.create_trade(db, Trade(**values))


async def close(db, trade: Trade, **kw) -> None:
    values = dict(
        exit_price=987.654321, fee_exit_usdt=0.013579, pnl_gross_usdt=124.567891,
        pnl_net_usdt=123.456789, close_reason="SENTINEL_REASON", closed_at=now() - H,
        slippage_exit_usdt=0.035791, fill_source="SENTINEL_FILL",
    )
    values.update(kw)
    await trades_repo.close_trade(db, trade.id, **values)


async def baseline(db, client=None, **kwargs):
    """Un notificador que ya tomo su linea base (el estado actual no se notifica)."""
    notifier, clock = make_notifier(db, client, **kwargs)
    assert await notifier.poll_once() == 0
    return notifier, clock


# --- linea base: nada del pasado se notifica ------------------------------------------------------


async def test_the_first_run_takes_the_baseline_and_notifies_nothing(db):
    open_trade = await new_trade(db)
    done = await new_trade(db, "ETHUSDT", Side.SHORT)
    await close(db, done)
    await control_actions.apply_control(db, "kill", channel="DASHBOARD", actor="renzo")
    await system_state_repo.set_state(
        db, "circuit_breaker_until", (now() + 5 * H).isoformat(),
    )
    await universe_repo.insert_snapshot(db, [AssetUniverseEntry(
        refreshed_at=now() - H, coingecko_id="bitcoin", symbol="BTCUSDT", coingecko_rank=1,
        included=True, has_bitunix_perp=True,
    )])
    client = FakeClient()
    notifier, _ = make_notifier(db, client)

    assert await notifier.poll_once() == 0
    assert await notifier.poll_once() == 0  # y tampoco despues

    assert client.sent == []
    state = json.loads(await system_state_repo.get_state(db, "push_state"))
    assert state["trades"] == {"max_id": done.id, "open_ids": [open_trade.id]}
    assert state["universe"]["included"] == ["BTCUSDT"]
    assert state["risk"]["kill"] is True and state["risk"]["paused"] is True
    assert state["risk"]["breaker_until"] is not None


async def test_an_empty_database_has_a_baseline_too(db):
    notifier, _ = await baseline(db)

    state = json.loads(await system_state_repo.get_state(db, "push_state"))
    assert state["trades"] == {"max_id": 0, "open_ids": []}
    assert state["universe"]["included"] == [] and state["risk"]["kill"] is False
    assert notifier.sent_total == 0


# --- posiciones ----------------------------------------------------------------------------------


async def test_a_new_position_is_announced_once_with_its_levels(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    trade = await new_trade(db)

    assert await notifier.poll_once() == 1
    assert await notifier.poll_once() == 0  # no se repite

    [text] = client.sent
    assert text.splitlines() == [
        "MODO PAPER TRADING",
        "Posicion ABIERTA (cuenta paper)",
        "BTCUSDT LONG | estrategia ema_cross_9_21 | origen LLM_APROBADA",
        "Entrada 64000.5 | cantidad 0.0007 | margen 5.00 USDT x10",
        "SL 63100 | TP 66000 | liquidacion estimada 58000",
        f"Abierta {trade.opened_at:%Y-%m-%d %H:%M} UTC (id {trade.id})",
    ]


async def test_a_position_without_levels_says_so(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await new_trade(db, sl_price=None, tp_price=None, liq_price=None, strategy=None,
                    decision_source=None)

    await notifier.poll_once()

    assert "sin SL | sin TP | sin liquidacion estimada" in client.sent[0]
    assert "estrategia n/d | origen n/d" in client.sent[0]


async def test_closing_hides_the_result_while_the_gate_is_closed_and_leaks_nothing(db, gate):
    trade = await new_trade(db)
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await close(db, trade, closed_at=now() - H)

    assert await notifier.poll_once() == 1

    [text] = client.sent
    assert text.splitlines()[:2] == [
        "MODO PAPER TRADING",
        f"Posicion cerrada: BTCUSDT LONG (estrategia ema_cross_9_21, id {trade.id})",
    ]
    assert "Abierta " in text and " | cerrada " in text
    assert text.endswith(f"Resultado, precio de salida y motivo: {HIDDEN_RESULTS}.")
    for sentinel in SENTINELS:
        assert sentinel not in text, sentinel
    for word in ("PnL", "Motivo", "salida "):
        assert word not in text.replace("precio de salida", "")


async def test_closing_shows_the_outcome_when_the_gate_is_open(db, gate):
    gate.open = True
    trade = await new_trade(db)
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await close(db, trade)

    await notifier.poll_once()

    [text] = client.sent
    assert "Motivo SENTINEL_REASON | salida 987.65432 | PnL neto +123.4568 USDT" in text
    assert "(+2469.1 % del margen)" in text
    assert HIDDEN_RESULTS not in text


async def test_a_position_opened_and_closed_between_two_polls_gets_both_messages(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    trade = await new_trade(db)
    await close(db, trade)

    assert await notifier.poll_once() == 2

    assert client.sent[0].startswith("MODO PAPER TRADING\nPosicion ABIERTA")
    assert client.sent[1].startswith("MODO PAPER TRADING\nPosicion cerrada")
    for sentinel in SENTINELS:
        assert all(sentinel not in text for text in client.sent)


async def test_the_gate_is_asked_once_per_poll_and_only_when_a_close_needs_it(db, gate):
    a = await new_trade(db, "BTCUSDT")
    b = await new_trade(db, "ETHUSDT", Side.SHORT)
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await new_trade(db, "SOLUSDT")
    await notifier.poll_once()
    assert gate.calls == 0  # una apertura no necesita la puerta

    await close(db, a)
    await close(db, b)
    await notifier.poll_once()

    assert gate.calls == 1 and len(client.sent) == 3


async def test_a_retroactive_close_is_announced_because_the_diff_is_by_state(db, gate):
    """La reconciliacion al reiniciar cierra con la hora del golpe de SL, anterior a todo cursor."""
    trade = await new_trade(db, opened_at=now() - 5 * H)
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await close(db, trade, closed_at=now() - 4 * H)  # horas antes de la linea base

    assert await notifier.poll_once() == 1

    assert "Posicion cerrada" in client.sent[0]


async def test_a_position_that_disappears_is_forgotten_silently(db, gate):
    trade = await new_trade(db)
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await db.execute("DELETE FROM trades WHERE id = ?", (trade.id,))

    assert await notifier.poll_once() == 0

    assert client.sent == []
    state = json.loads(await system_state_repo.get_state(db, "push_state"))
    assert state["trades"]["open_ids"] == []


# --- universo ------------------------------------------------------------------------------------


def snapshot(stamp, included, extra=()):
    rows = [
        AssetUniverseEntry(
            refreshed_at=stamp, coingecko_id=s.lower(), symbol=s, coingecko_rank=i + 1,
            included=True, has_bitunix_perp=True,
        )
        for i, s in enumerate(sorted(included))
    ]
    rows += [
        AssetUniverseEntry(
            refreshed_at=stamp, coingecko_id=s.lower(), symbol=s, coingecko_rank=50 + i,
            included=False, has_bitunix_perp=True,
        )
        for i, s in enumerate(extra)
    ]
    return rows


async def test_a_universe_change_is_announced_with_the_attribution(db, gate):
    await universe_repo.insert_snapshot(db, snapshot(now() - 48 * H, ["BTCUSDT", "XMRUSDT"]))
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    stamp = now() - H
    await universe_repo.insert_snapshot(db, snapshot(stamp, ["BTCUSDT", "LINKUSDT"], ["XMRUSDT"]))

    assert await notifier.poll_once() == 1
    assert await notifier.poll_once() == 0

    [text] = client.sent
    lines = text.splitlines()
    assert lines[0] == "MODO PAPER TRADING"
    assert lines[1] == f"Universo ACTUALIZADO ({stamp:%Y-%m-%d %H:%M} UTC)"
    assert "Entran: LINKUSDT" in lines and "Salen: XMRUSDT" in lines
    assert "Vigente (2): BTCUSDT, LINKUSDT" in lines
    assert lines[-1] == f"{COINGECKO_TEXT} ({COINGECKO_URL})" == (
        "Data provided by CoinGecko (https://www.coingecko.com)")
    assert any("no se cierra" in line for line in lines)


async def test_a_refresh_with_the_same_universe_is_not_announced(db, gate):
    await universe_repo.insert_snapshot(db, snapshot(now() - 48 * H, ["BTCUSDT", "ETHUSDT"]))
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await universe_repo.insert_snapshot(db, snapshot(now() - H, ["BTCUSDT", "ETHUSDT"]))

    assert await notifier.poll_once() == 0
    assert client.sent == []


async def test_a_change_back_is_announced_again(db, gate):
    await universe_repo.insert_snapshot(db, snapshot(now() - 72 * H, ["BTCUSDT", "XMRUSDT"]))
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await universe_repo.insert_snapshot(db, snapshot(now() - 48 * H, ["BTCUSDT", "LINKUSDT"]))
    await notifier.poll_once()
    await universe_repo.insert_snapshot(db, snapshot(now() - 24 * H, ["BTCUSDT", "XMRUSDT"]))

    await notifier.poll_once()

    assert len(client.sent) == 2 and "Entran: XMRUSDT" in client.sent[1]


# --- riesgo --------------------------------------------------------------------------------------


async def test_the_kill_switch_from_the_dashboard_is_one_alert_with_its_origin(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await control_actions.apply_control(db, "kill", channel="DASHBOARD", actor="renzo")

    assert await notifier.poll_once() == 1  # kill + pausa = un solo aviso

    [text] = client.sent
    assert "ALERTA: KILL SWITCH ACTIVADO. No se abren entradas nuevas." in text
    assert "La medicion esta DETENIDA (scheduler en pausa)." in text
    assert "Origen: dashboard (renzo) a las " in text and text.endswith(" UTC")


async def test_resume_after_a_kill_is_one_message_too(db, gate):
    await control_actions.apply_control(db, "kill", channel="DASHBOARD", actor="renzo")
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await control_actions.apply_control(db, "resume", channel="TELEGRAM", actor="telegram")

    assert await notifier.poll_once() == 1

    [text] = client.sent
    assert "Kill switch DESACTIVADO." in text and "La medicion sigue." in text
    assert "Origen: Telegram (telegram)" in text


async def test_a_plain_pause_and_resume_are_announced(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await control_actions.apply_control(db, "pause", channel="DASHBOARD", actor="renzo")
    await notifier.poll_once()
    await control_actions.apply_control(db, "resume", channel="DASHBOARD", actor="renzo")
    await notifier.poll_once()

    assert "Scheduler PAUSADO: la medicion esta DETENIDA" in client.sent[0]
    assert "Origen: dashboard (renzo)" in client.sent[0]
    assert "Scheduler REANUDADO: la medicion sigue." in client.sent[1]
    assert len(client.sent) == 2


async def test_an_old_control_record_is_not_claimed_as_the_origin(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await control_actions.apply_control(
        db, "pause", channel="DASHBOARD", actor="renzo", now=now() - 10 * timedelta(minutes=1),
    )

    await notifier.poll_once()

    assert "Scheduler PAUSADO" in client.sent[0] and "Origen" not in client.sent[0]


async def test_a_pause_that_changes_nothing_sends_nothing(db, gate):
    await control_actions.apply_control(db, "pause", channel="DASHBOARD", actor="renzo")
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await control_actions.apply_control(db, "pause", channel="DASHBOARD", actor="renzo")

    assert await notifier.poll_once() == 0


async def test_the_circuit_breaker_is_announced_on_and_off(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    until = now() + 5 * H
    await system_state_repo.set_state(db, "circuit_breaker_until", until.isoformat())
    await notifier.poll_once()
    await system_state_repo.set_state(db, "circuit_breaker_until", (now() - H).isoformat())
    await notifier.poll_once()

    assert f"ALERTA: CIRCUIT BREAKER ACTIVADO hasta {until:%Y-%m-%d %H:%M} UTC." in client.sent[0]
    assert "se levanta solo" in client.sent[0]
    assert "Circuit breaker terminado" in client.sent[1] and len(client.sent) == 2


async def test_the_hard_drawdown_stop_is_one_alert_and_its_counter_does_not_repeat_it(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "1")
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")

    assert await notifier.poll_once() == 1
    assert await notifier.poll_once() == 0
    await system_state_repo.set_state(db, "drawdown_stop_active", "0")
    await notifier.poll_once()

    assert "STOP POR DRAWDOWN ACTIVADO (modo duro)" in client.sent[0]
    assert "resume_drawdown_stop" in client.sent[0]
    assert "Stop por drawdown LEVANTADO" in client.sent[1] and len(client.sent) == 2


async def test_in_alert_mode_a_threshold_cross_warns_without_blocking(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client, settings=make_settings(DRAWDOWN_STOP_MODE="alerta"))
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "1")

    assert await notifier.poll_once() == 1

    assert "el drawdown cruzo el umbral de nuevo (modo alerta" in client.sent[0]
    assert "no bloquea" in client.sent[0]


async def test_a_drawdown_counter_change_in_hard_mode_is_synced_silently(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await system_state_repo.set_state(db, "drawdown_stop_would_have_triggered_count", "1")

    assert await notifier.poll_once() == 0
    assert await notifier.poll_once() == 0


async def test_the_daily_loss_limit_is_announced_on_and_off(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await system_state_repo.set_state(db, "daily_loss_limit_locked", "1")
    await notifier.poll_once()
    await system_state_repo.set_state(db, "daily_loss_limit_locked", "0")
    await notifier.poll_once()

    assert "ALERTA: LIMITE DE PERDIDA DIARIA alcanzado." in client.sent[0]
    assert "Limite de perdida diaria levantado" in client.sent[1]


async def test_risk_alerts_never_carry_result_derived_numbers(db, gate):
    """La racha de perdidas y el pico de equity existen en `system_state`: no deben salir."""
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    await system_state_repo.set_state(db, "consecutive_losses", "777")
    await system_state_repo.set_state(db, "equity_peak_usdt", "99999.5")
    await system_state_repo.set_state(db, "daily_loss_baseline_usdt", "88888.25")
    assert await notifier.poll_once() == 0  # cambiar esas claves no dispara nada
    await system_state_repo.set_state(db, "circuit_breaker_until", (now() + H).isoformat())
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")
    await system_state_repo.set_state(db, "daily_loss_limit_locked", "1")
    await control_actions.apply_control(db, "kill", channel="DASHBOARD", actor="renzo")

    await notifier.poll_once()

    assert len(client.sent) == 4
    for text in client.sent:
        for number in ("777", "99999", "88888"):
            assert number not in text


# --- entrega -------------------------------------------------------------------------------------


async def three_events(db):
    client = FakeClient()
    notifier, clock = await baseline(db, client)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        await new_trade(db, symbol)
    return notifier, clock, client


async def test_messages_are_spaced_by_more_than_a_second(db, gate):
    notifier, clock, client = await three_events(db)

    assert await notifier.poll_once() == 3

    spacing = [s for s in clock.sleeps if s > 0]
    assert len(client.sent) == 3 and len(spacing) == 2
    assert all(s == pytest.approx(push.MIN_SPACING_S) for s in spacing)


async def test_at_most_ten_messages_go_out_per_poll_and_the_rest_wait(db, gate):
    client = FakeClient()
    notifier, _ = await baseline(db, client)
    for n in range(12):
        await new_trade(db, f"S{n}USDT")

    assert await notifier.poll_once() == 10
    assert await notifier.poll_once() == 2
    assert await notifier.poll_once() == 0

    assert len(client.sent) == 12 and len(set(client.sent)) == 12  # ninguno repetido


async def test_a_transient_failure_in_the_middle_neither_repeats_nor_loses_messages(db, gate):
    ok, boom = None, TelegramApiError("sendMessage: HTTP 500", status_code=500)
    client = FakeClient(script=[ok, boom, boom, boom])
    notifier, _ = await baseline(db, client)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        await new_trade(db, symbol)

    first = await notifier.poll_once()  # el 1o sale; el 2o falla 3 veces seguidas

    assert first == 1 and len(client.sent) == 1
    assert notifier.consecutive_failures == 3 and notifier.last_error == "TelegramApiError 500"
    assert await notifier.poll_once() == 2  # reintenta el 2o y envia el 3o
    assert len(client.sent) == 3
    assert [t.splitlines()[2].split()[0] for t in client.sent] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert notifier.consecutive_failures == 0 and notifier.last_error is None


async def test_retry_after_from_telegram_is_respected_and_capped(db, gate):
    flood = TelegramApiError("sendMessage: HTTP 429", status_code=429, retry_after=7)
    huge = TelegramApiError("sendMessage: HTTP 429", status_code=429, retry_after=9999)
    client = FakeClient(script=[flood, huge])
    notifier, clock = await baseline(db, client)
    await new_trade(db)

    assert await notifier.poll_once() == 1

    waits = [s for s in clock.sleeps if s > 2.0]
    assert 7.0 in waits and push.MAX_WAIT_S in waits  # 9999 s se acota
    assert len(client.sent) == 1


async def test_a_permanent_error_drops_the_message_and_moves_on(db, gate, caplog):
    forbidden = TelegramApiError("sendMessage: HTTP 403", status_code=403)
    client = FakeClient(script=[forbidden])
    notifier, _ = await baseline(db, client)
    await new_trade(db, "BTCUSDT")
    await new_trade(db, "ETHUSDT", Side.SHORT)

    with caplog.at_level("ERROR", logger="app.telegram.push"):
        assert await notifier.poll_once() == 1  # el 1o se descarta, el 2o sale
        assert await notifier.poll_once() == 0  # y el descartado no vuelve a intentarse

    assert notifier.dropped_total == 1 and len(client.sent) == 1
    assert "ETHUSDT" in client.sent[0]
    assert any("descartada (TelegramApiError 403)" in r.getMessage() for r in caplog.records)
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


async def test_unexpected_client_errors_are_transient_not_fatal(db, gate):
    client = FakeClient(script=[RuntimeError("bug"), OSError("red"), None])
    notifier, _ = await baseline(db, client)
    await new_trade(db)

    assert await notifier.poll_once() == 1

    assert notifier.failed_total == 2 and len(client.sent) == 1


async def test_a_hung_send_times_out_instead_of_blocking(db, gate):
    class Hung(FakeClient):
        async def send_message(self, chat_id, text):
            await asyncio.Event().wait()

    notifier, _ = await baseline(db, Hung(), send_timeout_s=0.02)
    await new_trade(db)

    started = time.perf_counter()
    assert await notifier.poll_once() == 0  # 3 intentos agotados: queda para el proximo sondeo

    assert time.perf_counter() - started < 2.0
    assert notifier.failed_total == 3 and notifier.last_error == "TimeoutError"


async def test_after_a_failed_poll_the_message_is_retried_exactly_once_more(db, gate):
    boom = TelegramApiError("x", status_code=502)
    client = FakeClient(script=[boom, boom, boom])
    notifier, _ = await baseline(db, client)
    await new_trade(db)

    assert await notifier.poll_once() == 0
    assert await notifier.poll_once() == 1
    assert await notifier.poll_once() == 0

    assert len(client.sent) == 1


# --- estado persistido ---------------------------------------------------------------------------


async def test_a_restart_resumes_without_repeating_and_announces_what_happened_while_down(db, gate):
    client = FakeClient()
    first, _ = await baseline(db, client)
    await new_trade(db, "BTCUSDT")
    await first.poll_once()
    assert len(client.sent) == 1

    await new_trade(db, "ETHUSDT", Side.SHORT)  # ocurre con el bot apagado
    second_client = FakeClient()
    second, _ = make_notifier(db, second_client)

    assert await second.poll_once() == 1  # carga el estado: solo lo nuevo

    assert "ETHUSDT" in second_client.sent[0] and "BTCUSDT" not in second_client.sent[0]
    assert await second.poll_once() == 0


@pytest.mark.parametrize("garbage", [
    "{no es json", "[]", '{"version": 999}', '{"version": 1, "trades": {}}',
    json.dumps({"version": 1, "trades": {"max_id": "x", "open_ids": []},
                "universe": {"included": []}, "risk": {}}),
])
async def test_a_damaged_state_is_replaced_by_a_fresh_baseline(db, gate, garbage, caplog):
    await new_trade(db)
    await system_state_repo.set_state(db, "push_state", garbage)
    client = FakeClient()
    notifier, _ = make_notifier(db, client)

    with caplog.at_level("WARNING", logger="app.telegram.push"):
        assert await notifier.poll_once() == 0  # linea base nueva: no notifica el historial

    assert client.sent == []
    state = json.loads(await system_state_repo.get_state(db, "push_state"))
    assert state["version"] == 1 and len(state["trades"]["open_ids"]) == 1
    assert any("linea base nueva" in r.getMessage() for r in caplog.records)


async def test_the_notifier_only_ever_writes_its_own_state_key(db, gate):
    notifier, _ = await baseline(db)
    writes: list[tuple[str, tuple]] = []
    real = db.execute
    recording = False

    async def spy(query, params=()):
        if recording:
            writes.append((query, params))
        return await real(query, params)

    db.execute = spy  # type: ignore[method-assign]
    trade = await new_trade(db)
    await control_actions.apply_control(db, "kill", channel="DASHBOARD", actor="renzo")
    await close(db, trade)
    await universe_repo.insert_snapshot(db, snapshot(now() - H, ["BTCUSDT"]))
    recording = True
    try:
        assert await notifier.poll_once() >= 3
    finally:
        recording = False
        db.execute = real  # type: ignore[method-assign]

    assert writes, "debio guardar su estado"
    for query, params in writes:
        assert "system_state" in query and params[0] == "push_state", (query, params)


# --- arranque, mensaje inicial y bucle -----------------------------------------------------------


async def test_the_startup_message_confirms_the_channel_and_the_risk_state(db, gate):
    await control_actions.apply_control(db, "pause", channel="DASHBOARD", actor="renzo")
    client = FakeClient()
    notifier, _ = make_notifier(db, client)
    await notifier._ensure_state()

    await notifier.send_startup(True)
    await notifier.send_startup(False)

    first, second = client.sent
    assert first.splitlines()[0] == "MODO PAPER TRADING"
    assert "Notificaciones de Minerva ACTIVAS" in first
    assert "kill switch inactivo | scheduler PAUSADO | circuit breaker inactivo" in first
    assert "Linea base tomada ahora: no se avisan los eventos anteriores." in first
    assert "Se retoma desde el ultimo evento avisado." in second


async def test_the_loop_announces_startup_then_events_and_stops_cleanly(db, gate):
    client = FakeClient()
    settings = make_settings(TELEGRAM_PUSH_POLL_SECONDS=2)
    notifier, _ = make_notifier(db, client, settings=settings)
    task = asyncio.create_task(notifier.run_forever(startup_delay_s=0.0))

    for _ in range(100):
        if notifier.active and client.sent:
            break
        await asyncio.sleep(0.01)
    assert notifier.active is True and "ACTIVAS" in client.sent[0]
    notifier.stop()
    await asyncio.wait_for(task, timeout=2)


async def test_the_loop_survives_polls_that_raise_and_backs_off(db, gate, monkeypatch, caplog):
    notifier, _ = make_notifier(db, FakeClient())
    calls = {"n": 0}

    async def exploding():
        calls["n"] += 1
        raise RuntimeError("bug con clave=SECRETO")

    monkeypatch.setattr(notifier, "poll_once", exploding)
    task = asyncio.create_task(notifier.run_forever(startup_delay_s=0.0))
    with caplog.at_level("ERROR", logger="app.telegram.push"):
        for _ in range(100):
            if calls["n"]:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
    notifier.stop()
    await asyncio.wait_for(task, timeout=2)

    assert calls["n"] >= 1 and notifier._failed_polls >= 1
    assert notifier._next_wait() > notifier.poll_seconds  # espera creciente
    assert any("fallo un sondeo (RuntimeError)" in r.getMessage() for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


def test_the_wait_between_polls_grows_with_failures_and_is_capped():
    notifier = PushNotifier(None, make_settings(), FakeClient())  # type: ignore[arg-type]
    assert notifier._next_wait() == notifier.poll_seconds == 10.0
    notifier.consecutive_failures = 2
    assert notifier._next_wait() == 40.0
    notifier._failed_polls = 20
    assert notifier._next_wait() == push.MAX_POLL_BACKOFF_S


def test_the_poll_period_has_a_floor():
    low = PushNotifier(None, make_settings(TELEGRAM_PUSH_POLL_SECONDS=0), FakeClient())  # type: ignore[arg-type]
    assert low.poll_seconds == push.MIN_POLL_SECONDS


# --- salud ---------------------------------------------------------------------------------------


async def test_the_health_block_has_counters_and_the_error_type_but_no_secrets(db, gate):
    client = FakeClient(script=[TelegramApiError("x", status_code=500)] * 3)
    notifier, _ = await baseline(db, client)
    await new_trade(db)
    await notifier.poll_once()

    block = notifier.health_block()
    dumped = json.dumps(block)

    assert block["enabled"] is True and block["active"] is False
    assert block["failed_total"] == 3 and block["last_error"] == "TelegramApiError 500"
    assert block["sent_total"] == 0 and block["last_sent_at"] is None
    for secret in (TOKEN, CHAT_ID, "BTCUSDT", "Posicion"):
        assert secret not in dumped


# --- arranque aislado ----------------------------------------------------------------------------


def test_it_is_not_even_built_when_the_switch_is_off(db):
    assert push_runtime.build_push_notifier(
        make_settings(TELEGRAM_PUSH_ENABLED=False), db) is None


@pytest.mark.parametrize("missing", [{"TELEGRAM_BOT_TOKEN": ""}, {"TELEGRAM_CHAT_ID": ""}])
def test_without_token_or_chat_it_is_not_built_and_says_why(db, missing, caplog):
    with caplog.at_level("WARNING", logger="app.telegram.push_runtime"):
        assert push_runtime.build_push_notifier(make_settings(**missing), db) is None

    assert any("falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID" in r.getMessage()
               for r in caplog.records)


def test_building_it_never_raises_and_never_logs_the_token(db, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError(f"fallo construyendo el cliente con {TOKEN}")

    monkeypatch.setattr(push_runtime, "TelegramClient", broken)
    with caplog.at_level("ERROR", logger="app.telegram.push_runtime"):
        assert push_runtime.build_push_notifier(make_settings(), db) is None

    assert any("NO iniciadas (RuntimeError)" in r.getMessage() for r in caplog.records)
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


async def test_maybe_start_registers_exactly_one_task_only_when_enabled(db, monkeypatch):
    monkeypatch.setattr(push_runtime, "TelegramClient", lambda *a, **k: FakeClient())
    tasks: list[asyncio.Task] = []

    assert push_runtime.maybe_start_push_notifier(
        make_settings(TELEGRAM_PUSH_ENABLED=False), db, tasks) is None and tasks == []
    notifier = push_runtime.maybe_start_push_notifier(make_settings(), db, tasks)

    assert notifier is not None and len(tasks) == 1
    notifier.stop()
    await asyncio.wait_for(tasks[0], timeout=2)  # se detiene sola durante el retraso de arranque


async def test_a_notifier_that_dies_with_a_bug_does_not_propagate(caplog):
    class Exploding:
        async def run_forever(self):
            raise RuntimeError("bug con clave=SECRETO")

    with caplog.at_level("ERROR", logger="app.telegram.push_runtime"):
        await asyncio.wait_for(push_runtime.run_isolated(Exploding()), timeout=1)

    assert any("terminaron de forma inesperada (RuntimeError)" in r.getMessage()
               for r in caplog.records)
    assert all("SECRETO" not in r.getMessage() for r in caplog.records)


# --- un Telegram hostil no frena a nadie ---------------------------------------------------------


class HostileClient:
    """Falla de todas las formas posibles; la mitad de las veces se cuelga."""

    def __init__(self) -> None:
        self.calls = 0
        self.hung = asyncio.Event()

    async def send_message(self, chat_id, text) -> None:
        self.calls += 1
        if self.calls % 2 == 0:
            await self.hung.wait()
        raise [TelegramApiError("x", status_code=500), RuntimeError("bug"), OSError("red")][
            self.calls % 3]

    async def aclose(self) -> None:
        return None


async def test_a_hostile_telegram_never_slows_the_scheduler_the_labeler_or_the_monitor(
    db, monkeypatch,
):
    generation = {"cycles": 0}

    async def fake_generation(db_, rest_, settings_):
        generation["cycles"] += 1
        return []

    monkeypatch.setattr("app.core.scheduler.run_signal_generation_cycle", fake_generation)
    settings = monitor_settings(SCHEDULER_POLL_SECONDS=0)
    rest = FakeRest(last=100.0)
    backend = PaperBackend(db, rest, settings)
    labeler = CountingLabeler()
    scheduler = Scheduler(db, rest, backend, settings, live_labeler=labeler)
    monitor = PositionMonitor(db, rest, backend, settings)
    client = HostileClient()

    async def tiny_sleep(seconds):
        await asyncio.sleep(0.001)

    notifier = PushNotifier(
        db, make_settings(), client, sleep=tiny_sleep, monotonic=time.monotonic,
        send_timeout_s=0.02,
    )
    await notifier._ensure_state()  # linea base ANTES de abrir la posicion
    trade = await _open_long(backend)  # evento para el notificador
    await monitor._reload_trades()
    gaps: list[float] = []

    async def ticker():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.01)
            moment = time.perf_counter()
            gaps.append(moment - last)
            last = moment

    async def isolated_fast():
        """`run_isolated` sin el retraso de arranque de 15 s."""
        try:
            await notifier.run_forever(startup_delay_s=0.0)
        except asyncio.CancelledError:
            raise

    tasks = [
        asyncio.create_task(scheduler.run_forever()),
        asyncio.create_task(isolated_fast()),
        asyncio.create_task(ticker()),
    ]
    await asyncio.sleep(0.15)
    await monitor.handle_ws_message(_ws_ticker("BTCUSDT", 94.5))
    await asyncio.sleep(0.35)
    scheduler.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    closed = await trades_repo.get_trade(db, trade.id)
    assert closed.status == TradeStatus.CLOSED and closed.close_reason == "SL"
    assert client.calls >= 2  # Telegram estuvo fallando (y colgandose) todo el rato...
    assert generation["cycles"] >= 5 and labeler.cycle_starts >= 5  # ...y el scheduler avanzo
    assert scheduler.last_cycle_at is not None
    assert max(gaps) < 0.25  # el event loop nunca se bloqueo


# --- cableado en el lifespan real ----------------------------------------------------------------


class RecordingTelegramClient:
    instances: list[RecordingTelegramClient] = []

    def __init__(self, token, **kwargs) -> None:
        self.token = token
        self.sent: list[str] = []
        self.closed = False
        RecordingTelegramClient.instances.append(self)

    async def send_message(self, chat_id, text) -> None:
        self.sent.append(text)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def bot(tmp_path, monkeypatch):
    patch_lifespan(monkeypatch, tmp_path / "lifespan.db")
    RecordingTelegramClient.instances = []
    monkeypatch.setattr(push_runtime, "TelegramClient", RecordingTelegramClient)


def test_with_the_switch_off_there_is_no_notifier_and_health_says_so(bot, monkeypatch):
    monkeypatch.setattr(main.settings, "telegram_push_enabled", False)

    with TestClient(main.app) as client:
        assert main.app.state.push_notifier is None
        block = client.get("/health").json()["telegram_push"]

    assert block["enabled"] is False and block["active"] is False and block["sent_total"] == 0
    assert RecordingTelegramClient.instances == []


def test_with_the_switch_on_the_notifier_is_wired_isolated_and_closed_on_shutdown(
    bot, monkeypatch,
):
    monkeypatch.setattr(main.settings, "telegram_push_enabled", True)
    monkeypatch.setattr(main.settings, "telegram_bot_token", TOKEN)
    monkeypatch.setattr(main.settings, "telegram_chat_id", CHAT_ID)

    with TestClient(main.app) as client:
        notifier = main.app.state.push_notifier
        health = client.get("/health").json()

        assert notifier is not None
        assert health["telegram_push"]["enabled"] is True
        assert health["llm_labeling"]["frozen_hash_ok"] is True
        assert main.app.state.scheduler is not None and main.app.state.monitor is not None
        assert json.dumps(health["telegram_push"]).find(TOKEN) == -1

    assert RecordingTelegramClient.instances[0].closed is True
    assert RecordingTelegramClient.instances[0].sent == []  # el retraso de arranque no vencio


def test_lifespan_fakes_force_the_push_off_even_if_the_real_env_enabled_it(bot):
    assert main.settings.telegram_push_enabled is False


# --- estructura: nada del nucleo conoce al notificador -------------------------------------------


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


@pytest.mark.parametrize("name", ["push.py", "push_runtime.py"])
def test_the_notifier_does_not_import_the_core_of_the_measurement(name):
    forbidden = (
        "anthropic", "app.llm", "app.core.scheduler", "app.trading.shadow_live_labeler",
        "app.trading.shadow_book", "app.trading.paper_execution", "app.trading.position_monitor",
        "app.execution", "app.news", "app.memory", "app.trading.risk_engine.check_new_entry",
    )
    imported = _imports(REPO_ROOT / "app" / "telegram" / name)

    assert [m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)] == []


def test_the_notifier_has_no_write_statements_of_its_own():
    source = (REPO_ROOT / "app" / "telegram" / "push.py").read_text(encoding="utf-8")

    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER)\b", source)
    assert ".execute(" not in source and ".execute_many(" not in source
    assert source.count("set_state(") == 1  # la unica escritura: su propio estado


def test_no_module_of_the_core_imports_the_notifier():
    allowed = {"main.py", "deps.py", "health.py", "push_runtime.py", "push.py"}
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        if path.name in allowed:
            continue
        if any(m.startswith("app.telegram.push") for m in _imports(path)):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []
