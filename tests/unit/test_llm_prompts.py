"""Plantilla del prompt (subfase 3.6, fases iii-A y iv): JSON crudo exigido,
hash estable, contexto del prompt, y features desde datos ya guardados (indicadores,
niveles planeados, mercado), con nulos explicitos cuando falta algo."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.llm.prompts import (
    PROMPT_SHA256,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_features_from_shadow_trade,
    build_user_message,
    estimate_input_tokens,
)
from app.persistence.models import ShadowTrade, Side, SignalRecord, TradeStatus
from app.persistence.repositories import signals_repo

CANDLE = datetime(2026, 1, 1, 4, tzinfo=UTC)


def make_settings(**overrides) -> Settings:
    defaults = {"FUNDING_STALE_MARGIN_HOURS": 9.0}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_system_prompt_forbids_markdown_code_blocks():
    assert "```" in SYSTEM_PROMPT  # lo menciona para prohibirlo explicitamente
    assert "sin bloques de código" in SYSTEM_PROMPT


def test_system_prompt_gives_context_and_says_null_must_not_be_inferred():
    assert "paper trading" in SYSTEM_PROMPT.lower()
    assert "10x" in SYSTEM_PROMPT and "50 %" in SYSTEM_PROMPT
    assert "ema_cross_9_21" in SYSTEM_PROMPT  # explica que mide cada estrategia
    assert "null" in SYSTEM_PROMPT and "NUNCA lo infieras" in SYSTEM_PROMPT


def test_system_prompt_does_not_fix_a_target_approval_rate():
    assert "tasa de aprobación objetivo" in SYSTEM_PROMPT  # dice que NO tiene una
    # Revision del piloto v1 (90% APROBAR): sin agregar un porcentaje objetivo.
    assert "%" not in SYSTEM_PROMPT.split("tasa de aprobación objetivo")[1].split(".")[0]


def test_system_prompt_states_the_funding_sign_convention():
    # Revision del piloto v1: el modelo escribio dos veces "funding negativo
    # favorece al short" -- la convencion correcta (quien le paga a quien).
    assert "funding POSITIVO" in SYSTEM_PROMPT and "LONG pagan a los SHORT" in SYSTEM_PROMPT
    assert "funding NEGATIVO" in SYSTEM_PROMPT and "SHORT pagan a los LONG" in SYSTEM_PROMPT


def test_system_prompt_states_the_liquidation_vs_sl_ordering_is_never_an_inconsistency():
    # Revision del piloto v1: el modelo rechazo un SHORT por esto.
    assert "NUNCA una inconsistencia" in SYSTEM_PROMPT
    assert "DEBAJO del precio de entrada" in SYSTEM_PROMPT  # LONG
    assert "liquidación está por ENCIMA" in SYSTEM_PROMPT  # SHORT


def test_system_prompt_asks_to_name_the_main_risk_before_deciding():
    # Punto 4 de la revision: reduce complacencia, sin tasa objetivo.
    assert "PRINCIPAL riesgo concreto" in SYSTEM_PROMPT
    assert "no es, por sí mismo, un argumento a favor" in SYSTEM_PROMPT
    assert "tampoco cuenta como" in SYSTEM_PROMPT


def test_system_prompt_mentions_trend_context_and_asks_for_250_characters():
    assert "retorno de 24h" in SYSTEM_PROMPT and "72h" in SYSTEM_PROMPT
    assert "media móvil de 50 velas" in SYSTEM_PROMPT
    assert "máximo 250 caracteres" in SYSTEM_PROMPT


def test_prompt_version_is_v2():
    assert PROMPT_VERSION == "signal_review_v2"


def test_prompt_sha256_matches_the_system_prompt():
    assert PROMPT_SHA256 == hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def test_build_user_message_is_valid_sorted_json():
    text = build_user_message({"b": 1, "a": None})
    assert text == '{"a": null, "b": 1}'
    assert json.loads(text) == {"a": None, "b": 1}


def test_estimate_input_tokens_is_chars_over_two():
    assert estimate_input_tokens("abc", "defghi") == 4  # (3+6)//2


def _shadow_trade(key="BTCUSDT|LONG|c1", strategies=("ema_cross_9_21",)) -> ShadowTrade:
    return ShadowTrade(
        symbol="BTCUSDT", side=Side.LONG, strategy=strategies[0], status=TradeStatus.OPEN,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0,
        opened_at=CANDLE, contributing_strategies=list(strategies), signal_group_key=key,
        candle_close_time=CANDLE, sl_margin_loss_pct=50.0,
        sl_price=95.0, tp_price=110.0, trailing_distance=2.0, liq_price=90.5,
    )


@pytest.mark.asyncio
async def test_features_include_indicators_and_timeframe_from_an_existing_signal_row(db):
    await signals_repo.insert_signal(db, SignalRecord(
        symbol="BTCUSDT", strategy="ema_cross_9_21", timeframe="4h",
        candle_close_time=CANDLE, evaluated_at=CANDLE, signal="LONG", price_at_eval=100.0,
        indicators_json=json.dumps({"ema_fast": 101.2, "ema_slow": 99.8}),
    ))
    trade = _shadow_trade()

    features = await build_features_from_shadow_trade(db, trade, make_settings())

    assert features["indicators_by_strategy"]["ema_cross_9_21"] == {
        "ema_fast": 101.2, "ema_slow": 99.8,
    }
    assert features["timeframe_by_strategy"]["ema_cross_9_21"] == "4h"
    assert features["symbol"] == "BTCUSDT" and features["side"] == "LONG"


@pytest.mark.asyncio
async def test_funding_indicator_units_are_normalized_to_percent(db):
    """Revision del piloto v1: las columnas de `funding_contrarian*` guardan
    la FRACCION cruda (0,0001 = 0,01 %), distinta de `funding_rate_pct`
    (features de mercado, ya en porcentaje) -- el modelo confundio las dos.
    Se normalizan (y renombran con `_pct`) en vez de explicar la diferencia."""
    await signals_repo.insert_signal(db, SignalRecord(
        symbol="BTCUSDT", strategy="funding_contrarian_percentile_experimental",
        timeframe="4h", candle_close_time=CANDLE, evaluated_at=CANDLE, signal="SHORT",
        price_at_eval=100.0,
        indicators_json=json.dumps({
            "funding_rate": 0.0003, "funding_recent_avg": 0.00025,
            "funding_pctl_upper": 0.0004, "funding_pctl_lower": -0.0001,
            "funding_history_all_real": True,
        }),
    ))
    trade = _shadow_trade(strategies=("funding_contrarian_percentile_experimental",))

    features = await build_features_from_shadow_trade(db, trade, make_settings())

    indicators = features["indicators_by_strategy"]["funding_contrarian_percentile_experimental"]
    assert indicators["funding_rate_pct"] == pytest.approx(0.03)
    assert indicators["funding_recent_avg_pct"] == pytest.approx(0.025)
    assert indicators["funding_pctl_upper_pct"] == pytest.approx(0.04)
    assert indicators["funding_pctl_lower_pct"] == pytest.approx(-0.01)
    # Las claves crudas en fraccion no deben quedar, para no dejar las dos unidades.
    for raw_key in ("funding_rate", "funding_recent_avg", "funding_pctl_upper",
                    "funding_pctl_lower"):
        assert raw_key not in indicators
    # Un campo sin relacion con funding no se toca.
    assert indicators["funding_history_all_real"] is True


@pytest.mark.asyncio
async def test_features_include_the_planned_levels_from_the_shadow_trade(db):
    trade = _shadow_trade()
    features = await build_features_from_shadow_trade(db, trade, make_settings())
    assert features["sl_margin_loss_pct"] == 50.0
    assert features["sl_price"] == 95.0 and features["tp_price"] == 110.0
    assert features["liq_price_estimated"] == 90.5
    assert features["leverage"] == 10
    # trailing_distance ya NO se envia (piloto v1: null en las 21 respuestas,
    # ninguna estrategia activa lo usa hoy).
    assert "trailing_distance" not in features


@pytest.mark.asyncio
async def test_features_include_entry_price_and_its_derived_risk_distances(db):
    """Revision del piloto v1: el modelo no podia verificar el precio de
    entrada porque antes ni se enviaba. LONG: entry=100, sl=95, tp=110."""
    trade = _shadow_trade()
    features = await build_features_from_shadow_trade(db, trade, make_settings())
    assert features["entry_price_planned"] == 100.0
    assert features["sl_distance_pct"] == pytest.approx(5.0)
    assert features["tp_distance_pct"] == pytest.approx(10.0)
    assert features["risk_reward"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_risk_reward_is_null_without_a_tp(db):
    trade = _shadow_trade()
    trade.tp_price = None
    features = await build_features_from_shadow_trade(db, trade, make_settings())
    assert features["tp_distance_pct"] is None
    assert features["risk_reward"] is None
    assert features["sl_distance_pct"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_features_are_null_explicit_when_a_signal_row_is_missing(db):
    trade = _shadow_trade(strategies=("ema_cross_9_21", "donchian_breakout_20"))

    features = await build_features_from_shadow_trade(db, trade, make_settings())

    assert features["indicators_by_strategy"] == {
        "ema_cross_9_21": None, "donchian_breakout_20": None,
    }
    assert features["timeframe_by_strategy"] == {
        "ema_cross_9_21": None, "donchian_breakout_20": None,
    }


@pytest.mark.asyncio
async def test_market_features_are_null_explicit_without_any_cached_data(db):
    trade = _shadow_trade()
    features = await build_features_from_shadow_trade(db, trade, make_settings())
    for key in (
        "funding_rate_pct", "funding_is_approximated", "volatility_atr14_pct",
        "returns_std_30", "btc_correlation_30", "btc_correlation_100",
        "return_24h_pct", "return_72h_pct", "distance_to_ema50_1h_pct",
    ):
        assert features[key] is None, key
    # Las posiciones reales SI tienen un valor (0), no son null: no hay ninguna.
    assert features["open_real_positions_total"] == 0


@pytest.mark.asyncio
async def test_features_json_round_trips_through_build_user_message(db):
    trade = _shadow_trade()
    features = await build_features_from_shadow_trade(db, trade, make_settings())
    text = build_user_message(features)
    assert json.loads(text) == features
