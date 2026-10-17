"""E2E en proceso del bot de Telegram (subfase 3.7): cliente HTTP real sobre un
`httpx.MockTransport` que simula la Bot API (sin sockets), enrutador real y base
real en memoria. Recorre una conversacion completa."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from app.execution.paper_backend import PaperBackend
from app.llm.decision_service import LlmDecisionService
from app.telegram.bot import TelegramBot
from app.telegram.client import TelegramClient
from app.telegram.commands import BotContext, CommandRouter
from app.trading import control_state, risk_engine
from app.trading.shadow_live_labeler import ShadowLiveLabeler
from tests.unit.test_position_monitor import FakeRest, make_settings

OWNER = 777000111
TOKEN = "123456:E2E-SECRET"
NOW = 2_000_000.0


def update(update_id, text, chat_id=OWNER, chat_type="private"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id, "date": NOW - 1, "text": text,
            "chat": {"id": chat_id, "type": chat_type},
        },
    }


class FakeTelegramApi:
    """Simula `getUpdates` (con confirmacion por `offset`) y `sendMessage`."""

    def __init__(self, scripted_batches: list[list[dict]]) -> None:
        self.batches = list(scripted_batches)
        self.sent: list[dict] = []
        self.offsets: list[int | None] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        body = json.loads(request.content)
        if request.url.path.endswith("/getUpdates"):
            self.offsets.append(body.get("offset"))
            if not self.batches:
                # Como un long poll real: espera en vez de contestar al instante (sin
                # esto el bucle giraría sin ceder el control al event loop).
                await asyncio.sleep(0.02)
                return httpx.Response(200, json={"ok": True, "result": []})
            return httpx.Response(200, json={"ok": True, "result": self.batches.pop(0)})
        if request.url.path.endswith("/sendMessage"):
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404, json={"ok": False})


async def test_a_full_conversation_pauses_and_resumes_and_ignores_strangers(db):
    api = FakeTelegramApi([
        [update(1, "/kill SI")],  # acumulado ANTES de arrancar: se descarta, no se ejecuta
        [],  # cola vacia -> el bot ya puede atender
        [
            update(10, "/status"),
            update(11, "/pause", chat_id=555),  # un desconocido: ni respuesta ni efecto
            update(12, "/pause"),
            update(13, "/pause SI"),
            update(14, "/status"),
            update(15, "/kill SI", chat_id=555, chat_type="group"),
            update(16, "/resume"),
            update(17, "/resume SI"),
            update(18, "/metrics"),
        ],
    ])
    settings = make_settings()
    ctx = BotContext(
        db=db, settings=settings, paper_backend=PaperBackend(db, FakeRest(100.0), settings),
        scheduler=SimpleNamespace(last_cycle_at=datetime.now(UTC)),
        live_labeler=ShadowLiveLabeler(db, settings, LlmDecisionService(db, settings)),
        monitor=SimpleNamespace(marks={}),
    )
    client = TelegramClient(TOKEN, transport=httpx.MockTransport(api.handler))
    bot = TelegramBot(
        client=client, allowed_chat_id=OWNER, handler=CommandRouter(ctx), now=lambda: NOW,
        poll_timeout=0,
    )

    task = asyncio.create_task(bot.run_forever())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if len(api.sent) >= 7 and not api.batches:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    replies = [m["text"] for m in api.sent]
    assert all(m["chat_id"] == OWNER for m in api.sent)  # nunca se le respondio al desconocido
    assert len(replies) == 7  # /status, /pause, /pause SI, /status, /resume, /resume SI, /metrics
    assert "Scheduler pausado: no" in replies[0]
    assert "DETIENE LA MEDICION" in replies[1] and "/pause SI" in replies[1]
    assert "Scheduler PAUSADO" in replies[2]
    assert "Scheduler pausado: SI (la medicion esta detenida)" in replies[3]
    assert "/resume SI" in replies[4]
    assert "Scheduler REANUDADO" in replies[5]
    assert "Avance de la medicion" in replies[6] and "Gate CERRADO" in replies[6]
    # estado final: reanudado y sin kill switch (el /kill SI viejo y el ajeno no hicieron nada)
    assert await control_state.is_scheduler_paused(db) is False
    assert await risk_engine.is_kill_switch_active(db) is False
    # los updates viejos se confirmaron (offset) sin ejecutarse, y todos los demas tambien
    assert api.offsets[0] is None and api.offsets[1] == 2 and api.offsets[2] == 2
    assert api.offsets[3] == 19
    assert TOKEN not in json.dumps(api.sent)
