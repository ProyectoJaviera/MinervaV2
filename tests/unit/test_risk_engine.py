"""Tests de `app/trading/risk_engine.py` (Fase 3, subfase 3.2) -- motor de
riesgo en vivo: un test por limite, el escenario de racha de perdidas, la
persistencia entre reinicios y la prueba de que el kill switch nunca
bloquea el cierre de una posicion ya abierta."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.execution.paper_backend import PaperBackend
from app.persistence.database import Database
from app.persistence.models import Side, Trade, TradeStatus
from app.persistence.repositories import risk_rejections_repo, system_state_repo, trades_repo
from app.trading import risk_engine


class FakeRestClient:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        return [{"symbol": symbol, "lastPrice": str(self.price), "markPrice": str(self.price)}]


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0,
        LEVERAGE=10,
        TAKER_FEE_PCT=0.0006,
        MAKER_FEE_PCT=0.0002,
        INITIAL_CAPITAL_USDT=100.0,
        MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=3,
        MAX_SAME_DIRECTION_POSITIONS=2,
        MAX_DRAWDOWN_PCT=0.20,
        MAX_DAILY_LOSS_PCT=0.05,
        CONSECUTIVE_LOSSES_CIRCUIT_BREAKER=4,
        CIRCUIT_BREAKER_COOLDOWN_HOURS=8.0,
        LIVE_SL_MARGIN_CAP_PCT=50.0,
        DRAWDOWN_STOP_MODE="duro",
        REAL_ACCOUNT_ELIGIBLE_STRATEGIES="ema_cross_9_21,funding_contrarian_experimental",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


async def _open_trade(db: Database, symbol: str, side: Side, margin_usdt: float = 10.0) -> Trade:
    trade = Trade(
        symbol=symbol, side=side, strategy=None, status=TradeStatus.OPEN, leverage=10,
        margin_usdt=margin_usdt, notional_usdt=margin_usdt * 10, qty=1.0, entry_price=100.0,
        fee_entry_usdt=0.0, opened_at=datetime.now(UTC),
    )
    return await trades_repo.create_trade(db, trade)


# --- un test por limite --------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_when_nothing_blocks(db):
    settings = make_settings()
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert decision.allowed
    assert decision.approved_margin_usdt == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_kill_switch_blocks_new_entry(db):
    settings = make_settings()
    await risk_engine.activate_kill_switch(db)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "KILL_SWITCH_ACTIVE"
    rejections = await risk_rejections_repo.get_rejections(db, reason="KILL_SWITCH_ACTIVE")
    assert len(rejections) == 1


@pytest.mark.asyncio
async def test_drawdown_stop_duro_blocks_new_entry(db):
    settings = make_settings(DRAWDOWN_STOP_MODE="duro")
    await risk_engine.update_equity_tracking(db, settings, 100.0)  # fija el pico en 100
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=78.0,  # 22% de drawdown > 20%
    )
    assert not decision.allowed
    assert decision.reason == "DRAWDOWN_STOP_ACTIVE"
    assert await risk_engine.get_drawdown_stop_trigger_count(db) == 1


@pytest.mark.asyncio
async def test_drawdown_stop_alerta_mode_does_not_block_but_counts(db):
    # MAX_DAILY_LOSS_PCT holgado para aislar el efecto del drawdown -- una
    # caida de 22% tambien dispararia el limite de perdida diaria (5%
    # default) y esta prueba quiere ver SOLO el comportamiento del stop
    # por drawdown en modo "alerta".
    settings = make_settings(DRAWDOWN_STOP_MODE="alerta", MAX_DAILY_LOSS_PCT=0.50)
    await risk_engine.update_equity_tracking(db, settings, 100.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=78.0,
    )
    assert decision.allowed  # "alerta" nunca bloquea
    assert await risk_engine.get_drawdown_stop_trigger_count(db) == 1  # pero SI cuenta


@pytest.mark.asyncio
async def test_drawdown_stop_resume_is_manual_only(db):
    """En modo "duro" el stop NO se levanta solo aunque el equity se
    recupere -- solo con `resume_drawdown_stop` explicito."""
    settings = make_settings(DRAWDOWN_STOP_MODE="duro")
    await risk_engine.update_equity_tracking(db, settings, 100.0)
    await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=78.0,
    )
    # El equity se "recupera" por completo -- el stop sigue activo.
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "DRAWDOWN_STOP_ACTIVE"

    await risk_engine.resume_drawdown_stop(db)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert decision.allowed


@pytest.mark.asyncio
async def test_circuit_breaker_active_blocks_new_entry(db):
    settings = make_settings()
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await system_state_repo.set_state(db, "circuit_breaker_until", until)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "CIRCUIT_BREAKER_ACTIVE"


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_new_entry(db):
    settings = make_settings(MAX_DAILY_LOSS_PCT=0.05)
    # Primera consulta del dia -- fija la linea base en 100.
    await risk_engine.update_equity_tracking(db, settings, 100.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=94.0,  # 6% de perdida > 5%
    )
    assert not decision.allowed
    assert decision.reason == "DAILY_LOSS_LIMIT"


@pytest.mark.asyncio
async def test_strategy_not_eligible_blocks_new_entry(db):
    settings = make_settings()  # eligibles: ema_cross_9_21, funding_contrarian_experimental
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy="donchian_breakout_20",
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "STRATEGY_NOT_ELIGIBLE"


@pytest.mark.asyncio
async def test_strategy_none_bypasses_eligibility_filter(db):
    """Una entrada sin estrategia identificada (p.ej. manual) no se
    bloquea por este filtro -- es sobre estrategias CONOCIDAS, no una
    exigencia de que toda entrada declare una."""
    settings = make_settings()
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert decision.allowed


@pytest.mark.asyncio
async def test_max_simultaneous_positions_blocks_new_entry(db):
    settings = make_settings(MAX_SIMULTANEOUS_POSITIONS=3, MAX_SAME_DIRECTION_POSITIONS=10)
    await _open_trade(db, "BTCUSDT", Side.LONG)
    await _open_trade(db, "ETHUSDT", Side.SHORT)
    await _open_trade(db, "SOLUSDT", Side.LONG)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="XRPUSDT", side=Side.SHORT, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "MAX_SIMULTANEOUS_POSITIONS"
    assert decision.details == {"open": 3, "limit": 3}


@pytest.mark.asyncio
async def test_max_same_direction_positions_blocks_new_entry(db):
    """Cupo total con espacio (2 de 5), pero el cupo por DIRECCION (2) ya
    esta lleno -- debe bloquear por esta razon especifica, no colarse."""
    settings = make_settings(MAX_SIMULTANEOUS_POSITIONS=5, MAX_SAME_DIRECTION_POSITIONS=2)
    await _open_trade(db, "BTCUSDT", Side.LONG)
    await _open_trade(db, "ETHUSDT", Side.LONG)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="SOLUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "MAX_SAME_DIRECTION_POSITIONS"


@pytest.mark.asyncio
async def test_sl_cap_exceeded_blocks_new_entry(db):
    settings = make_settings(LIVE_SL_MARGIN_CAP_PCT=50.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0, sl_margin_loss_pct=60.0,
    )
    assert not decision.allowed
    assert decision.reason == "SL_CAP_EXCEEDED"


@pytest.mark.asyncio
async def test_sl_cap_none_skips_the_check(db):
    """`sl_margin_loss_pct=None` (todavia no hay generador de senales,
    subfase 3.3) omite este chequeo por completo, no lo bloquea."""
    settings = make_settings(LIVE_SL_MARGIN_CAP_PCT=50.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0, sl_margin_loss_pct=None,
    )
    assert decision.allowed


@pytest.mark.asyncio
async def test_margin_unavailable_blocks_new_entry(db):
    """Cupo de posiciones y de direccion con espacio de sobra, pero el
    limite por activo (10% de 100 = 10 USDT) ya esta consumido en ESE
    simbolo -- debe bloquear por margen, no por cupo de posiciones."""
    settings = make_settings(
        MAX_SIMULTANEOUS_POSITIONS=5, MAX_SAME_DIRECTION_POSITIONS=5,
        MAX_CAPITAL_PCT_PER_ASSET=0.10,
    )
    await _open_trade(db, "BTCUSDT", Side.LONG, margin_usdt=10.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=100.0,
    )
    assert not decision.allowed
    assert decision.reason == "MARGIN_UNAVAILABLE"


# --- racha de perdidas / circuit breaker --------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_triggers_at_nth_loss_and_auto_resumes_after_cooldown(db):
    settings = make_settings(
        CONSECUTIVE_LOSSES_CIRCUIT_BREAKER=3, CIRCUIT_BREAKER_COOLDOWN_HOURS=8.0,
    )

    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=99.0)
    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=98.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=98.0,
    )
    assert decision.allowed, "2 perdidas todavia no deben disparar el breaker (umbral=3)"

    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=97.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=97.0,
    )
    assert not decision.allowed
    assert decision.reason == "CIRCUIT_BREAKER_ACTIVE"

    # Simula que ya paso el enfriamiento (sin esperar horas reales).
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await system_state_repo.set_state(db, "circuit_breaker_until", past)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=97.0,
    )
    assert decision.allowed, "debe auto-reanudarse tras el enfriamiento, sin accion manual"


@pytest.mark.asyncio
async def test_winning_trade_resets_the_consecutive_losses_streak(db):
    settings = make_settings(CONSECUTIVE_LOSSES_CIRCUIT_BREAKER=3)
    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=99.0)
    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=98.0)
    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=5.0, current_equity=103.0)
    # La racha se reinicio -- 2 perdidas nuevas no deben disparar el breaker.
    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=102.0)
    await risk_engine.record_trade_closed(db, settings, pnl_net_usdt=-1.0, current_equity=101.0)
    decision = await risk_engine.check_new_entry(
        db, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
        margin_usdt=10.0, current_equity=101.0,
    )
    assert decision.allowed


# --- persistencia entre reinicios ---------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_state_persists_across_a_simulated_restart(tmp_path):
    """Cierra la conexion por completo y abre una NUEVA -- nada queda en
    memoria del proceso, asi que esto simula un reinicio real del bot."""
    db_path = str(tmp_path / "risk_engine_restart.db")
    settings = make_settings()

    db1 = Database(db_path)
    await db1.connect()
    try:
        await risk_engine.activate_kill_switch(db1)
    finally:
        await db1.close()

    db2 = Database(db_path)
    await db2.connect()
    try:
        decision = await risk_engine.check_new_entry(
            db2, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
            margin_usdt=10.0, current_equity=100.0,
        )
        assert not decision.allowed
        assert decision.reason == "KILL_SWITCH_ACTIVE"
    finally:
        await db2.close()


@pytest.mark.asyncio
async def test_drawdown_stop_state_persists_across_a_simulated_restart(tmp_path):
    db_path = str(tmp_path / "risk_engine_restart_dd.db")
    settings = make_settings(DRAWDOWN_STOP_MODE="duro")

    db1 = Database(db_path)
    await db1.connect()
    try:
        await risk_engine.update_equity_tracking(db1, settings, 100.0)
        await risk_engine.check_new_entry(
            db1, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
            margin_usdt=10.0, current_equity=78.0,
        )
    finally:
        await db1.close()

    db2 = Database(db_path)
    await db2.connect()
    try:
        decision = await risk_engine.check_new_entry(
            db2, settings, symbol="BTCUSDT", side=Side.LONG, strategy=None,
            margin_usdt=10.0, current_equity=100.0,
        )
        assert not decision.allowed
        assert decision.reason == "DRAWDOWN_STOP_ACTIVE"
    finally:
        await db2.close()


# --- kill switch nunca bloquea un cierre ---------------------------------


@pytest.mark.asyncio
async def test_kill_switch_active_does_not_block_closing_an_open_position(db):
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    backend = PaperBackend(db, rest_client, settings)
    trade = await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
    )

    await risk_engine.activate_kill_switch(db)

    with pytest.raises(risk_engine.RiskRejectedError):
        await backend.open_position(
            "ETHUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        )

    rest_client.price = 110.0
    closed = await backend.close_position(trade.id, reason="MANUAL")
    assert closed.status == TradeStatus.CLOSED
    assert closed.pnl_net_usdt > 0


# --- "dia" en zona horaria configurable ----------------------------------


def test_today_key_uses_the_configured_timezone_not_naive_utc():
    now = datetime(2026, 6, 15, 2, 30, tzinfo=UTC)
    expected = now.astimezone(ZoneInfo("America/Santiago")).strftime("%Y-%m-%d")
    assert risk_engine.today_key("America/Santiago", now=now) == expected
    # Confirma que de verdad usa la zona configurada, no la fecha UTC
    # "ingenua" -- en este caso puntual deben diferir.
    assert expected != now.strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_drawdown_trigger_count_increments_once_per_episode_not_continuously(db):
    settings = make_settings(DRAWDOWN_STOP_MODE="alerta")
    await risk_engine.update_equity_tracking(db, settings, 100.0)
    await risk_engine.update_equity_tracking(db, settings, 78.0)  # entra en breach
    await risk_engine.update_equity_tracking(db, settings, 77.0)  # sigue en breach
    await risk_engine.update_equity_tracking(db, settings, 76.0)  # sigue en breach
    assert await risk_engine.get_drawdown_stop_trigger_count(db) == 1

    await risk_engine.update_equity_tracking(db, settings, 95.0)  # se recupera
    await risk_engine.update_equity_tracking(db, settings, 78.0)  # entra en breach otra vez
    assert await risk_engine.get_drawdown_stop_trigger_count(db) == 2


# --- smoke test de punta a punta (regla de CLAUDE.md) -------------------


@pytest.mark.asyncio
async def test_smoke_end_to_end_risk_engine_lifecycle(tmp_path):
    """Un solo escenario que encadena todo el motor de riesgo de la
    subfase 3.2 contra una base pequena y sintetica (nunca la real):
    abrir -> bloquear por cupo -> racha de perdidas dispara el circuit
    breaker -> kill switch -> reinicio simulado -> el cierre de la
    posicion ya abierta sigue funcionando en todo momento."""
    db_path = str(tmp_path / "smoke_risk_engine.db")
    settings = make_settings(
        MAX_SIMULTANEOUS_POSITIONS=1, MAX_SAME_DIRECTION_POSITIONS=1,
        CONSECUTIVE_LOSSES_CIRCUIT_BREAKER=2, CIRCUIT_BREAKER_COOLDOWN_HOURS=8.0,
        # Holgado a proposito: dos perdidas de ~5 USDT cada una sobre 100
        # de capital ya se acercan al 5% default -- este smoke test quiere
        # aislar cupo/circuit-breaker/kill-switch, no la perdida diaria
        # (que ya tiene su propio test dedicado).
        MAX_DAILY_LOSS_PCT=0.50,
    )
    rest_client = FakeRestClient(price=100.0)
    db = Database(db_path)
    await db.connect()
    try:
        backend = PaperBackend(db, rest_client, settings)

        # 1. Abrir una posicion (estrategia elegible).
        trade = await backend.open_position(
            "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        )
        assert trade.status == TradeStatus.OPEN

        # 2. Una segunda entrada se bloquea por cupo de posiciones (1/1
        # lleno) Y queda auditada en risk_rejections.
        with pytest.raises(risk_engine.RiskRejectedError) as exc_info:
            await backend.open_position(
                "ETHUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
            )
        assert exc_info.value.reason == "MAX_SIMULTANEOUS_POSITIONS"
        rejections = await risk_rejections_repo.get_rejections(db)
        assert len(rejections) == 1

        # 3. Se cierra con perdida (libera el cupo) y una racha de 2
        # perdidas dispara el circuit breaker.
        rest_client.price = 95.0
        closed = await backend.close_position(trade.id, reason="MANUAL")
        assert closed.pnl_net_usdt < 0

        trade2 = await backend.open_position(
            "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        )
        rest_client.price = 90.0
        closed2 = await backend.close_position(trade2.id, reason="MANUAL")
        assert closed2.pnl_net_usdt < 0

        with pytest.raises(risk_engine.RiskRejectedError) as exc_info:
            await backend.open_position(
                "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
            )
        assert exc_info.value.reason == "CIRCUIT_BREAKER_ACTIVE"

        # 4. Kill switch manual, encima del circuit breaker.
        await risk_engine.activate_kill_switch(db)
    finally:
        await db.close()

    # 5. Reinicio simulado: conexion nueva, nada en memoria del proceso.
    db = Database(db_path)
    await db.connect()
    try:
        backend = PaperBackend(db, rest_client, settings)

        # El kill switch sigue activo tras el "reinicio".
        with pytest.raises(risk_engine.RiskRejectedError) as exc_info:
            await backend.open_position(
                "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
            )
        assert exc_info.value.reason == "KILL_SWITCH_ACTIVE"

        # Pero no hay ninguna posicion abierta que gestionar en este caso
        # -- confirmamos el otro sentido (cierre nunca bloqueado) con una
        # entrada manual directa a la tabla, simulando una posicion que
        # quedo abierta antes del kill switch.
        manual_trade = await _open_trade(db, "SOLUSDT", Side.SHORT, margin_usdt=10.0)
        closed_manual = await backend.close_position(manual_trade.id, reason="MANUAL")
        assert closed_manual.status == TradeStatus.CLOSED
    finally:
        await db.close()
