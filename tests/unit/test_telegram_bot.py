"""Bot de Telegram (subfase 3.7): cliente HTTP, allowlist estricta y bucle de
long-polling con aislamiento. La red de Telegram SIEMPRE esta simulada
(`httpx.MockTransport` / un cliente falso): ninguna prueba toca la red."""

from __future__ import annotations

import asyncio
import json
import traceback

import httpx
import pytest

from app.telegram.bot import (
    BACKOFF_MAX_SECONDS,
    INTERNAL_ERROR_TEXT,
    TelegramBot,
    is_allowed_update,
)
from app.telegram.client import MAX_MESSAGE_CHARS, TelegramApiError, TelegramClient

TOKEN = "123456:SECRET-TOKEN-ABC"
OWNER = 777000111
NOW = 1_000_000.0


def msg(update_id, chat_id=OWNER, text="/status", chat_type="private", date=NOW - 5):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id, "date": date, "text": text,
            "chat": {"id": chat_id, "type": chat_type}, "from": {"id": chat_id},
        },
    }


# --- cliente HTTP -------------------------------------------------------------------


def _client(handler) -> TelegramClient:
    return TelegramClient(TOKEN, transport=httpx.MockTransport(handler))


async def test_get_updates_sends_the_documented_parameters_and_returns_the_result():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": [msg(5)]})

    client = _client(handler)
    updates = await client.get_updates(offset=5, timeout=30)
    await client.aclose()

    assert seen["path"] == f"/bot{TOKEN}/getUpdates"
    assert seen["body"] == {
        "timeout": 30, "limit": 100, "allowed_updates": ["message"], "offset": 5,
    }
    assert updates == [msg(5)]


async def test_get_updates_omits_offset_when_none():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": []})

    client = _client(handler)
    await client.get_updates(offset=None, timeout=0)
    await client.aclose()

    assert "offset" not in seen["body"]


async def test_send_message_sends_chat_id_and_truncates_to_the_documented_limit():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = _client(handler)
    await client.send_message(OWNER, "x" * 5000)
    await client.aclose()

    assert seen["path"] == f"/bot{TOKEN}/sendMessage"
    assert seen["body"]["chat_id"] == OWNER
    assert len(seen["body"]["text"]) == MAX_MESSAGE_CHARS == 4096


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}),
        httpx.Response(409, json={"ok": False, "error_code": 409, "description": "Conflict"}),
        httpx.Response(429, json={"ok": False, "error_code": 429,
                                  "parameters": {"retry_after": 3}}),
        httpx.Response(200, json={"ok": False}),
        httpx.Response(500, text="<html>boom</html>"),
        httpx.Response(200, text="no es json"),
        httpx.Response(200, json=["no", "es", "dict"]),
    ],
)
async def test_any_non_ok_response_becomes_a_telegram_api_error_without_the_token(response):
    client = _client(lambda request: response)

    with pytest.raises(TelegramApiError) as excinfo:
        await client.get_updates(offset=None, timeout=0)
    await client.aclose()

    assert TOKEN not in str(excinfo.value)
    assert TOKEN not in "".join(traceback.format_exception(excinfo.value))


async def test_network_errors_never_leak_the_url_or_the_token():
    def handler(request):
        raise httpx.ConnectError(f"fallo conectando a {request.url}")

    client = _client(handler)
    with pytest.raises(TelegramApiError) as excinfo:
        await client.send_message(OWNER, "hola")
    await client.aclose()

    rendered = "".join(traceback.format_exception(excinfo.value))
    assert "ConnectError" in str(excinfo.value)
    assert TOKEN not in rendered and "api.telegram.org" not in rendered


# --- allowlist ---------------------------------------------------------------------------


def test_is_allowed_update_requires_a_private_chat_with_the_exact_id_and_text():
    assert is_allowed_update(msg(1), OWNER)
    assert is_allowed_update(msg(1), str(OWNER))  # el .env guarda el id como texto
    assert not is_allowed_update(msg(1, chat_id=999), OWNER)
    assert not is_allowed_update(msg(1, chat_type="group"), OWNER)
    assert not is_allowed_update(msg(1, chat_type="supergroup"), OWNER)
    assert not is_allowed_update(msg(1, chat_type="channel"), OWNER)
    assert not is_allowed_update({"update_id": 1, "edited_message": msg(1)["message"]}, OWNER)
    no_text = msg(1)
    del no_text["message"]["text"]
    assert not is_allowed_update(no_text, OWNER)
    assert not is_allowed_update({"update_id": 1}, OWNER)
    assert not is_allowed_update({"update_id": 1, "message": "x"}, OWNER)
    assert not is_allowed_update(msg(1, chat_id=OWNER + 1), OWNER)  # no es prefijo/aproximado


# --- bucle -----------------------------------------------------------------------------------


class FakeClient:
    """Cliente falso: `script` es la lista de respuestas de `get_updates` en orden
    (lista de updates o una excepcion a lanzar); sin mas respuestas queda a la espera."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[tuple[int | None, int]] = []
        self.sent: list[tuple] = []
        self.send_error: Exception | None = None
        self.closed = False

    async def get_updates(self, *, offset, timeout, limit=100):
        self.calls.append((offset, timeout))
        if not self.script:
            await asyncio.sleep(3600)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_message(self, chat_id, text):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((chat_id, text))

    async def aclose(self):
        self.closed = True


class Recorder:
    def __init__(self, reply="ok", error: Exception | None = None) -> None:
        self.texts: list[str] = []
        self.reply = reply
        self.error = error

    async def __call__(self, text: str):
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return self.reply


async def run_bot(client, handler, *, sleeps=None, until=lambda: False):
    """Corre el bucle hasta agotar el guion del cliente (y `until`) y lo cancela."""
    delays = sleeps if sleeps is not None else []

    async def fake_sleep(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)

    bot = TelegramBot(
        client=client, allowed_chat_id=OWNER, handler=handler, now=lambda: NOW, sleep=fake_sleep,
    )
    task = asyncio.create_task(bot.run_forever())
    for _ in range(400):
        await asyncio.sleep(0)
        if not client.script and until():
            break
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return bot, delays


async def test_foreign_and_group_chats_are_ignored_in_silence():
    client = FakeClient([
        [],  # drenaje inicial
        [
            msg(1, chat_id=424242),
            msg(2, chat_type="group"),
            msg(3, chat_id=424242, text="/kill SI"),
        ],
    ])
    handler = Recorder()

    bot, _ = await run_bot(client, handler)

    assert handler.texts == []  # ningun comando se ejecuto
    assert client.sent == []  # ni una respuesta: no se confirma que el bot existe
    assert bot.ignored_count == 3


async def test_the_owner_chat_is_processed_and_answered():
    client = FakeClient([[], [msg(1, text="/status"), msg(2, text="/metrics")]])
    handler = Recorder(reply="respuesta")

    await run_bot(client, handler)

    assert handler.texts == ["/status", "/metrics"]
    assert client.sent == [(OWNER, "respuesta"), (OWNER, "respuesta")]


async def test_pending_updates_from_before_startup_are_drained_not_executed():
    client = FakeClient([
        [msg(10, text="/kill SI"), msg(11, text="/pause SI")],  # acumulados durante una caida
        [],  # cola vacia -> listo
        [msg(12, text="/status")],
    ])
    handler = Recorder()

    await run_bot(client, handler)

    assert handler.texts == ["/status"]
    assert client.calls[0] == (None, 0) and client.calls[1] == (12, 0)  # drena con timeout 0
    assert client.calls[2][1] == 30  # y luego long polling


async def test_stale_messages_are_ignored():
    client = FakeClient([[], [msg(1, date=NOW - 500), msg(2, date=NOW - 10)]])
    handler = Recorder()

    await run_bot(client, handler)

    assert handler.texts == ["/status"] and len(client.sent) == 1


async def test_offset_advances_past_every_processed_update():
    client = FakeClient([[], [msg(20), msg(21)], [msg(22)]])
    handler = Recorder()

    await run_bot(client, handler)

    offsets = [offset for offset, _ in client.calls]
    assert offsets[:4] == [None, None, 22, 23]


async def test_a_broken_handler_answers_with_an_internal_error_and_keeps_polling():
    client = FakeClient([[], [msg(1)], [msg(2)]])
    handler = Recorder(error=RuntimeError("bug en el comando"))

    await run_bot(client, handler)

    assert len(handler.texts) == 2  # el segundo update tambien se proceso
    assert client.sent == [(OWNER, INTERNAL_ERROR_TEXT)] * 2


async def test_a_failing_reply_never_stops_the_loop():
    client = FakeClient([[], [msg(1)], [msg(2)]])
    client.send_error = TelegramApiError("sendMessage: HTTP 429", status_code=429)
    handler = Recorder()

    await run_bot(client, handler)

    assert len(handler.texts) == 2


async def test_network_failures_back_off_exponentially_with_a_cap_and_recover(caplog):
    errors = [TelegramApiError("getUpdates: ConnectError") for _ in range(12)]
    client = FakeClient([*errors, [], [msg(1)]])
    handler = Recorder()
    delays: list[float] = []

    with caplog.at_level("INFO"):
        await run_bot(client, handler, sleeps=delays)

    assert delays[:9] == [1, 2, 4, 8, 16, 32, 64, 128, 256]
    assert delays[9:12] == [BACKOFF_MAX_SECONDS] * 3  # tope de 5 minutos
    assert handler.texts == ["/status"]  # se recupero y siguio
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1  # un WARNING por racha, no uno por intento
    assert any("recuperado" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "error",
    [
        TelegramApiError("getUpdates: HTTP 401", status_code=401),  # token invalido
        TelegramApiError("getUpdates: HTTP 409", status_code=409),  # otro consumidor/webhook
        TelegramApiError("getUpdates: HTTP 429", status_code=429),
        TelegramApiError("getUpdates: respuesta no JSON"),
        KeyError("update_id"),  # update mal formado
        RuntimeError("cualquier cosa"),
    ],
)
async def test_every_kind_of_failure_is_contained_and_the_loop_keeps_going(error):
    client = FakeClient([[], error, [msg(1)]])
    handler = Recorder()

    await run_bot(client, handler)

    assert handler.texts == ["/status"]


async def test_logs_never_contain_the_token(caplog):
    real_client = TelegramClient(
        TOKEN, transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError(str(request.url)))
        ),
    )

    with caplog.at_level("DEBUG"):
        bot = TelegramBot(
            client=real_client, allowed_chat_id=OWNER, handler=Recorder(), now=lambda: NOW,
            sleep=lambda s: asyncio.sleep(0),
        )
        task = asyncio.create_task(bot.run_forever())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert caplog.records
    assert all(TOKEN not in r.getMessage() and "SECRET-TOKEN" not in str(r.exc_info)
               for r in caplog.records)


async def test_cancelling_the_task_closes_the_client():
    client = FakeClient([[]])

    await run_bot(client, Recorder())

    assert client.closed is True


async def test_stop_ends_the_loop_cleanly():
    client = FakeClient([[], [], [], []])
    bot = TelegramBot(
        client=client, allowed_chat_id=OWNER, handler=Recorder(), now=lambda: NOW,
    )
    bot.stop()

    await asyncio.wait_for(bot.run_forever(), timeout=1)

    assert client.closed is True
