"""Arnes de las pruebas de PUNTA A PUNTA (Fase 6, docs/FASE6_PLAN.md).

Arma el sistema REAL sobre una base de archivo temporal -- scheduler, generador de senales con
las estrategias reales, libro de sombras, etiquetador con el servicio de decision real, brazo
ejecutado, `PaperBackend`, motor de riesgo, monitor de posiciones, enrutador de comandos de
Telegram, notificador push y la aplicacion FastAPI real (`app.main.app`, llamada por ASGI SIN
lifespan) -- y simula SOLO el exterior: Anthropic (cliente programable que decide por simbolo),
Bitunix REST (precios; las velas se siembran en la cache), el WebSocket (los ticks se inyectan) y
Telegram (un cliente que registra los mensajes). Cero red y cero gasto; el `.env` real nunca se
lee (`Settings(_env_file=None)`).

Reloj: el generador usa `time.time()` real y el etiquetador `datetime.now()` real, asi que las
velas se siembran de modo que la ULTIMA cierre "hace unos segundos": el retraso de decision real
queda en segundos (y se prueba la regla de 60 s sembrando una vela cerrada hace mas tiempo).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app import main as app_main
from app.api.security import hash_password
from app.config import Settings
from app.core.heartbeat import Heartbeat
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.llm.client import LlmRawResponse
from app.llm.decision_service import LlmDecisionService
from app.persistence.database import Database
from app.persistence.models import AssetUniverseEntry, ContractSpec, Side
from app.persistence.repositories import ohlcv_repo, specs_repo, universe_repo
from app.telegram.commands import BotContext, CommandRouter
from app.telegram.push import PushNotifier
from app.trading.paper_execution import PaperExecutionArm
from app.trading.position_monitor import PositionMonitor
from app.trading.shadow_book import ShadowBook
from app.trading.shadow_live_labeler import ShadowLiveLabeler
from app.trading.signal_generator import SignalCandidate
from tests.e2e import series

PASSWORD = "e2e-clave-de-prueba"
JWT_SECRET = "e2e-secreto-de-prueba-" + "x" * 40
USERNAME = "admin"
_PASSWORD_HASH = hash_password(PASSWORD)  # bcrypt cuesta ~0.2 s: una sola vez por proceso


def make_settings(**overrides: Any) -> Settings:
    """Ajustes del escenario (por alias, como el `.env`); nunca lee el `.env` real."""
    values: dict[str, Any] = {
        "INITIAL_CAPITAL_USDT": 100.0, "DEFAULT_MARGIN_USDT": 5.0, "LEVERAGE": 10,
        "AUTO_OPEN_WITHOUT_LLM": False, "LLM_LIVE_LABELING_ENABLED": True,
        "PAPER_EXECUTION_ENABLED": True, "NEWS_COLLECTION_ENABLED": False,
        "LESSONS_MEMORY_ENABLED": False, "TELEGRAM_COMMANDS_ENABLED": False,
        "TELEGRAM_PUSH_ENABLED": True, "TELEGRAM_BOT_TOKEN": "0:E2E-FAKE", "TELEGRAM_CHAT_ID": "1",
        "ANTHROPIC_API_KEY": "", "REAL_ACCOUNT_ELIGIBLE_STRATEGIES": "ema_cross_9_21",
        "AUTH_USERNAME": USERNAME, "AUTH_PASSWORD_HASH": _PASSWORD_HASH, "JWT_SECRET": JWT_SECRET,
        "LLM_DAILY_BUDGET_USD": 1.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


# --- el exterior simulado -------------------------------------------------------------------------


class ScriptedRest:
    """Bitunix REST falso: precio ultimo y mark programables por simbolo; cualquier llamada que el
    escenario no preve falla (asi una prueba nunca depende de la red sin darse cuenta)."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self.mark: dict[str, float] = {}
        self.calls: list[str] = []
        self.fail_tickers = False

    def set_price(self, symbol: str, last: float, mark: float | None = None) -> None:
        self.last[symbol] = last
        self.mark[symbol] = last if mark is None else mark

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        self.calls.append("get_tickers")
        if self.fail_tickers:
            raise ConnectionError("REST caido (simulado)")
        symbols = [symbol] if symbol is not None else list(self.last)
        return [
            {"symbol": s, "lastPrice": str(self.last[s]), "markPrice": str(self.mark[s])}
            for s in symbols if s in self.last
        ]

    async def get_funding_rate_history(self, *args: Any, **kwargs: Any) -> list[dict]:
        self.calls.append("get_funding_rate_history")
        return []

    async def get_kline(self, *args: Any, **kwargs: Any) -> list[dict]:
        self.calls.append("get_kline")
        return []

    async def aclose(self) -> None:
        pass

    def __getattr__(self, name: str):
        async def _fail(*args: Any, **kwargs: Any):
            self.calls.append(name)
            raise AssertionError(f"llamada de red inesperada en el escenario E2E: {name}")

        return _fail


class ScriptedLlm:
    """Anthropic falso. Decide por simbolo (el mensaje del usuario es el JSON de features, que trae
    `symbol` y `side`); `decisions[simbolo]` puede ser "APROBAR", "RECHAZAR" o una excepcion que se
    relanza (timeout, HTTP). Registra cada llamada: sin red, sin gasto."""

    def __init__(self, default: str = "APROBAR") -> None:
        self.default = default
        self.decisions: dict[str, str | Exception] = {}
        self.calls: list[dict[str, Any]] = []
        self.delay_s = 0.0  # latencia simulada (para probar un apagado a mitad de llamada)

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int):
        features = json.loads(user)
        self.calls.append({"symbol": features["symbol"], "side": features["side"], "model": model})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        decision = self.decisions.get(features["symbol"], self.default)
        if isinstance(decision, Exception):
            raise decision
        text = json.dumps({"decision": decision, "confianza": 0.8, "razonamiento": "e2e"})
        return LlmRawResponse(text=text, input_tokens=1100, output_tokens=40, latency_ms=800)


class FakeTelegramPush:
    """Cliente de Telegram del notificador push: registra lo que se enviaria al chat."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: Any, text: str) -> None:
        self.sent.append(text)

    async def aclose(self) -> None:
        pass


async def _no_sleep(_seconds: float) -> None:
    return None


@dataclass
class Market:
    """Un simbolo del escenario: su precio y, si `signal` no es None, la senal 4h de su ultima
    vela."""

    price: float = 100.0
    signal: Side | None = None
    min_trade_volume: float = 0.001
    base_precision: int = 3


# --- el mundo ------------------------------------------------------------------------------------


class World:
    """Todo el sistema sobre una base temporal. Se crea con `await World.start(tmp_path, ...)` y se
    cierra con `await world.stop()` (siempre en `finally`: una base de archivo abierta cuelga el
    proceso al salir en Windows)."""

    def __init__(self, db_path: Path, settings: Settings) -> None:
        self.db_path = db_path
        self.settings = settings
        self.rest = ScriptedRest()
        self.llm = ScriptedLlm()
        self.push_client = FakeTelegramPush()
        self.universe: dict[str, Market] = {}
        self.api: httpx.AsyncClient | None = None
        self.powered = False
        self._announced = 0

    @classmethod
    async def start(cls, tmp_path: Path, **settings: Any) -> World:
        tmp_path.mkdir(parents=True, exist_ok=True)
        world = cls(tmp_path / "e2e.db", make_settings(**settings))
        await world._open(first_start=True)
        return world

    # --- ciclo de vida -----------------------------------------------------------------------

    async def _open(self, *, first_start: bool, now_ms: int | None = None) -> None:
        self.powered = True
        self.db = Database(str(self.db_path))
        await self.db.connect()
        s = self.settings
        self.backend = PaperBackend(self.db, self.rest, s)
        self.shadow = ShadowBook(self.db, s)
        self.monitor = PositionMonitor(self.db, self.rest, self.backend, s, shadow=self.shadow)
        self.arm = PaperExecutionArm(self.db, s, self.backend)
        service = LlmDecisionService(self.db, s, client_factory=lambda _settings: self.llm)
        self.labeler = ShadowLiveLabeler(self.db, s, service, on_decision=self.arm.submit)
        self.scheduler = Scheduler(
            self.db, self.rest, self.backend, s, ws_client=None, shadow=self.shadow,
            live_labeler=self.labeler,
        )
        self.heartbeat = Heartbeat(s.heartbeat_seconds)
        self.heartbeat.beat()
        # Mismo orden que el lifespan real: reconciliar ANTES de abrir el feed y el generador.
        self.reconcile_outcomes = await self.monitor.reconcile_on_startup(now_ms=now_ms)
        await self.arm.activate()
        await self.monitor.start_feed()
        self.router = CommandRouter(BotContext(
            db=self.db, settings=s, paper_backend=self.backend, scheduler=self.scheduler,
            live_labeler=self.labeler, monitor=self.monitor, paper_arm=self.arm,
        ))
        self.push = PushNotifier(self.db, s, self.push_client, sleep=_no_sleep)
        if first_start:
            await self.push.poll_once()  # linea base de una base vacia: nada que avisar
        self._mount_api()

    def _mount_api(self) -> None:
        state = app_main.app.state
        state.db, state.settings = self.db, self.settings
        state.heartbeat, state.scheduler = self.heartbeat, self.scheduler
        state.live_labeler, state.paper_execution = self.labeler, self.arm
        state.paper_backend, state.monitor = self.backend, self.monitor
        state.push_notifier = self.push
        state.news_collector = None
        state.lessons_runner = None
        self.api = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_main.app), base_url="http://testserver",
        )

    async def _shutdown_components(self, *, drain: bool = True) -> None:
        if not self.powered:
            return
        self.powered = False
        if drain:
            await self.drain()
        await self.labeler.shutdown()
        await self.arm.shutdown()
        await self.monitor.shutdown()
        await self.db.close()

    async def power_off(self, *, drain: bool = True) -> None:
        """Apaga el proceso (cierre ordenado del lifespan). Con `drain=False` las tareas de
        etiquetado / brazo en vuelo se CANCELAN, como al detener el bot a mitad de una llamada."""
        await self._shutdown_components(drain=drain)
        if self.api is not None:
            await self.api.aclose()
            self.api = None

    async def power_on(self, *, now_ms: int | None = None) -> None:
        """Vuelve a arrancar sobre LA MISMA base (instancias nuevas de todo; el exterior simulado --
        precios, guion del LLM -- se conserva). `now_ms` fija el instante de la reconciliacion."""
        assert not self.powered, "ya esta encendido"
        await self._open(first_start=False, now_ms=now_ms)

    async def restart(self, *, now_ms: int | None = None) -> None:
        await self.power_off()
        await self.power_on(now_ms=now_ms)

    @asynccontextmanager
    async def offline_db(self) -> AsyncIterator[Database]:
        """La base abierta por fuera del proceso, SOLO mientras esta apagado (simula lo que paso
        durante la caida: el tiempo que corrio, las velas que se cerraron)."""
        assert not self.powered, "apaga el sistema antes de tocar la base por fuera"
        database = Database(str(self.db_path))
        await database.connect()
        try:
            yield database
        finally:
            await database.close()

    async def stop(self) -> None:
        """Cierre de la prueba: cancela lo que siga en vuelo en vez de esperarlo."""
        try:
            await self._shutdown_components(drain=False)
        finally:
            if self.api is not None:
                await self.api.aclose()
                self.api = None
            for name in (
                "db", "settings", "heartbeat", "scheduler", "live_labeler", "paper_execution",
                "paper_backend", "monitor", "push_notifier", "news_collector", "lessons_runner",
            ):
                app_main.app.state._state.pop(name, None)

    # --- mercado -----------------------------------------------------------------------------

    async def seed_market(self, markets: dict[str, Market], *, close_age_s: float = 2.0) -> None:
        """Siembra universo, contratos y velas (1h, 4h y 1d) de `markets`. La ultima vela de cada
        serie cerro hace `close_age_s`. Los simbolos se acumulan en el universo vigente."""
        self.universe.update(markets)
        end_ms = int(time.time() * 1000) - int(close_age_s * 1000)
        now = datetime.now(UTC)
        await universe_repo.insert_snapshot(self.db, [
            AssetUniverseEntry(
                refreshed_at=now, coingecko_id=symbol.removesuffix("USDT").lower(), symbol=symbol,
                included=True, has_bitunix_perp=True,
            )
            for symbol in self.universe
        ])
        for symbol, market in markets.items():
            self.rest.set_price(symbol, market.price)
            await specs_repo.upsert_spec(self.db, ContractSpec(
                symbol=symbol, min_trade_volume=market.min_trade_volume,
                base_precision=market.base_precision, funding_interval_hours=8, fetched_at=now,
            ))
            closes_4h = (
                list(series.find_trend_cross_closes(market.signal, market.price))
                if market.signal is not None else series.neutral_4h_closes(market.price)
            )
            for interval, closes in (
                ("4h", closes_4h),
                ("1h", series.flat_closes(series.BARS_1H, market.price)),
                ("1d", series.flat_closes(series.BARS_1D, market.price)),
            ):
                await ohlcv_repo.upsert_bars(self.db, series.bars_from_closes(
                    closes, symbol=symbol, interval=interval, end_ms=end_ms,
                ))

    # --- conducir el sistema ---------------------------------------------------------------------

    async def drain(self) -> None:
        """Espera a que terminen las tareas de fuego y olvido (etiquetado -> brazo)."""
        for _ in range(200):
            tasks = [*self.labeler._tasks, *self.arm._tasks]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)
        raise AssertionError("las tareas de etiquetado / brazo no terminan")

    async def wait_until(self, condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
        """Espera (cediendo el control) a que se cumpla `condition`; falla si no llega a tiempo."""
        deadline = time.monotonic() + timeout_s
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError("la condicion esperada no se cumplio a tiempo")
            await asyncio.sleep(0.01)

    async def cycle(self) -> list[SignalCandidate]:
        """Un ciclo del scheduler y todo lo que dispara (sombra, etiqueta, brazo)."""
        candidates = await self.scheduler.run_cycle()
        await self.drain()
        return candidates

    async def sync(self) -> None:
        """Ciclo periodico del monitor: carga las posiciones y sombras abiertas, marcas, equity."""
        await self.monitor.tick_periodic()

    async def tick(self, symbol: str, price: float, *, mark: float | None = None) -> None:
        """Un tick del WebSocket publico (`tickers`) y el mismo precio en el REST simulado."""
        self.rest.set_price(symbol, price, mark)
        await self.monitor.handle_ws_message(
            {"ch": "tickers", "data": [{"s": symbol, "la": str(price)}]}
        )

    def expire_monitor_caches(self) -> None:
        """Salta las esperas de reloj de monitor (cache de ticker 2 s, confirmacion de
        liquidacion 5 s) que no tiene sentido esperar en una prueba."""
        self.monitor._ticker_cache.clear()
        self.monitor._liq_checked_at.clear()

    # --- lectura ---------------------------------------------------------------------------------

    async def query(self, sql: str, *params: Any) -> list[dict]:
        return [dict(row) for row in await self.db.fetch_all(sql, params)]

    async def trades(self, status: str | None = None) -> list[dict]:
        where, params = ("WHERE status = ?", (status,)) if status else ("", ())
        return await self.query(f"SELECT * FROM trades {where} ORDER BY id", *params)

    async def shadows(self, status: str | None = None) -> list[dict]:
        where, params = ("WHERE status = ?", (status,)) if status else ("", ())
        return await self.query(f"SELECT * FROM shadow_trades {where} ORDER BY id", *params)

    async def arm_rows(self) -> list[dict]:
        return await self.query("SELECT * FROM paper_execution_log ORDER BY id")

    async def state(self, key: str) -> str | None:
        rows = await self.query("SELECT value FROM system_state WHERE key = ?", key)
        return rows[0]["value"] if rows else None

    # --- los canales -----------------------------------------------------------------------------

    async def login(self) -> None:
        assert self.api is not None
        response = await self.api.post(
            "/api/auth/login", json={"username": USERNAME, "password": PASSWORD},
        )
        assert response.status_code == 200, response.text

    async def get(self, path: str) -> httpx.Response:
        assert self.api is not None
        return await self.api.get(path)

    async def control(self, action: str, phrase: str | None = None) -> httpx.Response:
        """Un POST de control del dashboard con la palabra de confirmacion (la exacta si no se
        indica otra)."""
        assert self.api is not None
        words = {"pause": "PAUSAR", "resume": "REANUDAR", "kill": "KILL"}
        return await self.api.post(
            f"/api/control/{action}", json={"confirm": phrase or words[action]},
        )

    async def say(self, text: str) -> str | None:
        """Un mensaje al bot de Telegram (el enrutador real)."""
        return await self.router(text)

    async def telegram_control(self, command: str) -> list[str | None]:
        """El flujo de confirmacion de Telegram: `/pause` y luego `/pause SI`."""
        return [await self.say(command), await self.say(f"{command} SI")]

    async def notifications(self) -> list[str]:
        """Avisos push NUEVOS desde la ultima vez (un sondeo del notificador real)."""
        await self.push.poll_once()
        fresh = self.push_client.sent[self._announced:]
        self._announced = len(self.push_client.sent)
        return fresh

    async def daily_summary(self) -> str:
        """El resumen diario por Telegram (`scripts/telegram_daily.py` real) sobre ESTA base, con
        el `/health` del bot en vivo y el envio capturado en vez de enviarse."""
        from scripts import telegram_daily

        health = (await self.get("/health")).json()
        sent: list[str] = []

        def capture(token: str, chat_id: str, message: str, timeout: float = 10.0) -> bool:
            sent.append(message)
            return True

        delivered = telegram_daily.run(
            host="127.0.0.1", port=8001, timeout=1.0, db_path=str(self.db_path), token="0:E2E",
            chat_id="1", fetch_health_fn=lambda host, port, timeout: (health, None),
            send_fn=capture,
        )
        assert delivered is True and len(sent) == 1
        return sent[0]

