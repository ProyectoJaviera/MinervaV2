"""E2E: con la puerta de resultados CERRADA (el estado real de hoy) ningun canal muestra
resultados de operaciones -- ni la API de lectura (que consume el dashboard), ni las rutas
heredadas, ni `/health`, ni Telegram, ni los avisos push, ni el resumen diario -- despues de que
la cadena completa cerro operaciones por TP, SL, trailing y liquidacion, y de una racha con su
post-mortem. Y la prueba de sensibilidad demuestra que el barrido no es ciego: con la puerta
abierta SI lo encuentra todo.

El frontend consume exactamente estas respuestas de la API; su propia defensa (no mostrar
resultados aunque el JSON los trajera) esta cubierta por las pruebas de Vitest."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.memory.runtime import LessonsRunner
from app.persistence.models import Side
from app.trading import measurement_status, verdict_record
from tests.e2e import leaks
from tests.e2e.world import Market, World

WIDE = {"MAX_SIMULTANEOUS_POSITIONS": 4, "MAX_SAME_DIRECTION_POSITIONS": 4}


async def refresh_marks(world: World) -> None:
    world.expire_monitor_caches()
    await world.sync()


async def tp_and_sl(make_world) -> World:
    """Un TP, un SL (corto) y una posicion que sigue ABIERTA con PnL flotante."""
    world = await make_world(**WIDE)
    await world.seed_market({
        "BTCUSDT": Market(100.0, Side.LONG), "ETHUSDT": Market(100.0, Side.SHORT),
        "SOLUSDT": Market(100.0, Side.LONG),
    })
    await world.cycle()
    await world.sync()
    await world.tick("BTCUSDT", 110.1)  # TP
    await world.tick("ETHUSDT", 105.5)  # SL
    await world.tick("SOLUSDT", 103.0)  # sigue abierta, con ganancia flotante
    await refresh_marks(world)
    assert sorted(t["close_reason"] for t in await world.trades("CLOSED")) == ["SL", "TP"]
    assert [t["symbol"] for t in await world.trades("OPEN")] == ["SOLUSDT"]
    return world


async def trailing(make_world) -> World:
    world = await make_world(REAL_ACCOUNT_ELIGIBLE_STRATEGIES="trend_atr_stop_9_21_50", **WIDE)
    await world.seed_market({
        "BTCUSDT": Market(100.0, Side.LONG), "ETHUSDT": Market(100.0, Side.SHORT),
    })
    await world.cycle()
    await world.sync()
    for symbol, sign in (("BTCUSDT", 1), ("ETHUSDT", -1)):
        distance = next(t for t in await world.trades("OPEN") if t["symbol"] == symbol)[
            "trailing_distance"
        ]
        best = 100.0 * (1 + sign * 0.05)
        await world.tick(symbol, best)
        await world.tick(symbol, best - sign * (distance + 0.2))
    await refresh_marks(world)
    assert [t["close_reason"] for t in await world.trades("CLOSED")] == ["TRAILING"] * 2
    return world


async def liquidation(make_world) -> World:
    world = await make_world(BACKTEST_FALLBACK_SL_PCT=0.10, LIVE_SL_MARGIN_CAP_PCT=100.0)
    await world.seed_market({"BTCUSDT": Market(100.0, Side.LONG)})
    await world.cycle()
    await world.sync()
    world.expire_monitor_caches()
    await world.tick("BTCUSDT", 90.2, mark=90.1)
    await refresh_marks(world)
    assert [t["close_reason"] for t in await world.trades("CLOSED")] == ["LIQUIDATION"]
    return world


async def streak(make_world) -> World:
    """Cuatro perdidas seguidas: breaker, post-mortem, lecciones y memoria con PnL en la base."""
    world = await make_world(LESSONS_MEMORY_ENABLED=True, MAX_DAILY_LOSS_PCT=0.5,
                             MAX_DRAWDOWN_PCT=0.9, **WIDE)
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    await world.seed_market({s: Market(100.0, Side.LONG) for s in symbols})
    await world.cycle()
    await world.sync()
    for symbol in symbols:
        await world.tick(symbol, 94.0)
    summary = await LessonsRunner(world.db, world.settings).run_once()
    assert summary["postmortem"] is True
    await refresh_marks(world)
    assert [t["close_reason"] for t in await world.trades("CLOSED")] == ["SL"] * 4
    return world


SCENARIOS = {
    "tp_sl_y_posicion_abierta": tp_and_sl, "trailing": trailing, "liquidacion": liquidation,
    "racha_y_postmortem": streak,
}


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_with_the_gate_closed_no_channel_shows_any_result(make_world, name):
    world = await SCENARIOS[name](make_world)

    channels = await leaks.collect_channels(world)
    sentinels = await leaks.sentinels_for(world)

    # La prueba no es vacua: hay operaciones cerradas, muchos canales y centinelas reales.
    assert len(sentinels) >= 6, sentinels
    assert len(channels) >= 25
    assert any(name.startswith("push #") and "cerrada" in ch.text.lower()
               for name, ch in channels.items()), "ningun aviso de cierre que revisar"
    problems = leaks.all_leaks(channels, sentinels)
    assert problems == [], problems

    # Lo que SI se ve: la estructura de la cuenta (niveles, horas) y el aviso de ocultos.
    account = channels["/api/account"].parsed
    assert account["reveal_allowed"] is False and account["results"] is None
    assert "ocultos" in channels["telegram /balance"].text
    assert "ocultos" in next(c.text for n, c in channels.items() if "cerrada" in c.text.lower()
                             and n.startswith("push #"))


def open_the_gate(monkeypatch, docs_dir: Path) -> None:
    """Gate de la medicion abierto + un registro oficial REAL del veredicto en un directorio
    temporal: todo canal pasa por `is_outcome_reveal_allowed`, que lee ambas cosas en cada
    llamada."""
    (docs_dir / "VEREDICTO_3_6_REGISTRO_2099-01-01.txt").write_text(
        "- **Veredicto: INCONCLUSO**\n", encoding="utf-8",
    )
    monkeypatch.setattr(verdict_record, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(measurement_status, "is_measurement_gate_open", lambda status: True)


async def test_the_sweep_is_not_blind_it_finds_the_results_once_the_gate_opens(
    make_world, monkeypatch, tmp_path,
):
    docs = tmp_path / "docs"
    docs.mkdir()
    open_the_gate(monkeypatch, docs)
    world = await tp_and_sl(make_world)

    channels = await leaks.collect_channels(world)
    sentinels = await leaks.sentinels_for(world)
    found = leaks.all_leaks(channels, sentinels)

    # Los tres detectores encuentran cosas...
    assert any("clave prohibida" in f for f in found)
    assert any("centinela" in f for f in found)
    assert leaks.revealed_labels(channels) == set(leaks.REVEAL_ONLY_TEXT)  # las 8 etiquetas
    # ... y en los canales esperados.
    assert {f.split(":")[0] for f in found} >= {
        "/api/account", "/api/risk", "/trades", "telegram /status", "telegram /balance",
        "telegram /positions", "telegram /metrics",
    }
    account = channels["/api/account"].parsed
    assert account["reveal_allowed"] is True and account["results"] is not None
    assert any(f.startswith("push #") for f in found)  # el aviso de cierre trae el resultado
    assert leaks.all_leaks({"/health": channels["/health"]}, sentinels) == []  # /health nunca


async def test_a_record_without_a_valid_verdict_does_not_open_the_gate(
    make_world, monkeypatch, tmp_path,
):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "VEREDICTO_3_6_REGISTRO_2099-01-01.txt").write_text("borrador sin veredicto\n")
    # el placebo tiene otro nombre a proposito: no es el registro oficial
    (docs / "PLACEBO_2099-01-01.txt").write_text("- **Veredicto: APORTA_VALOR**\n")
    monkeypatch.setattr(verdict_record, "DOCS_DIR", docs)
    monkeypatch.setattr(measurement_status, "is_measurement_gate_open", lambda status: True)
    world = await tp_and_sl(make_world)

    channels = await leaks.collect_channels(world)

    assert leaks.all_leaks(channels, await leaks.sentinels_for(world)) == []
    assert channels["/api/account"].parsed["reveal_allowed"] is False
