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
    FEATURES_VERSION,
    PROMPT_SHA256,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_features_from_shadow_trade,
    build_user_message,
    estimate_input_tokens,
)
from app.persistence.models import OHLCVBar, ShadowTrade, Side, SignalRecord, TradeStatus
from app.persistence.repositories import ohlcv_repo, signals_repo

CANDLE = datetime(2026, 1, 1, 4, tzinfo=UTC)
HOUR_MS = 3_600_000


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


def test_prompt_version_is_v3():
    assert PROMPT_VERSION == "signal_review_v3"


def test_features_version_is_tracked_separately_from_prompt_version():
    # Revision del piloto v2 (docs/FASE3_6_LLM.md seccion k): las dos
    # etiquetas son independientes a proposito (una es del texto del
    # sistema, la otra del mensaje/features).
    assert FEATURES_VERSION == "features_v1"


def test_prompt_sha256_covers_both_the_system_prompt_and_the_features_version():
    expected = hashlib.sha256(
        f"{SYSTEM_PROMPT}\n---FEATURES_VERSION---\n{FEATURES_VERSION}".encode()
    ).hexdigest()
    assert PROMPT_SHA256 == expected


def test_prompt_sha256_changes_if_features_version_changes_with_the_same_text():
    """Revision del piloto v2: antes PROMPT_SHA256 solo hasheaba el texto, asi
    que un cambio de unidades/campos del mensaje (features_version) no lo
    cambiaba -- measurement_keys podia mezclar dos mensajes distintos."""
    with_v1 = hashlib.sha256(
        f"{SYSTEM_PROMPT}\n---FEATURES_VERSION---\nfeatures_v1".encode()
    ).hexdigest()
    with_v2 = hashlib.sha256(
        f"{SYSTEM_PROMPT}\n---FEATURES_VERSION---\nfeatures_v2".encode()
    ).hexdigest()
    assert with_v1 != with_v2


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
        "returns_std_30_pct", "btc_correlation_30", "btc_correlation_100",
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


# --- convencion de unidades: toda clave "_pct" esta en porcentaje -----------
# Revision del piloto v2 (docs/FASE3_6_LLM.md seccion k): `volatility_atr14_pct`
# tenia el sufijo `_pct` pero devolvia la fraccion cruda (~100 veces mas chica);
# el modelo la leyo literal como porcentaje. Guarda de regresion GENERAL (no
# solo del caso puntual del ATR): con un escenario deliberadamente volatil
# (para que ninguna feature salga null ni legitimamente cero), ninguna clave
# "_pct" puede ser sospechosamente chica -- si alguien agrega una feature
# nueva y se olvida de multiplicar por 100, esta prueba debe notarlo.
_PCT_SANITY_FLOOR = 0.05  # bien por debajo de lo esperado en este escenario,
# bien por encima de lo que daria una fraccion cruda sin convertir por error.


@pytest.mark.asyncio
async def test_every_pct_suffixed_feature_is_in_percent_not_a_raw_fraction(db):
    # Velas 1h con una tendencia marcada, retornos VARIABLES (no un mismo %
    # cada vela -- eso daria desviacion cero, ver test de abajo con ese caso
    # a proposito) y mechas notorias: ATR, retornos y distancia a la EMA
    # quedan todos lejos de cero en este escenario.
    bars = []
    as_of_ms = int(CANDLE.timestamp() * 1000)
    price = 100.0
    for i in range(80):
        open_time = as_of_ms - (80 - i) * HOUR_MS
        price *= 1.03 if i % 2 == 0 else 1.005
        close = price
        bars.append(OHLCVBar(
            symbol="BTCUSDT", interval="1h", price_type="LAST_PRICE", open_time=open_time,
            open=close * 0.99, high=close * 1.03, low=close * 0.97, close=close,
            base_vol=1.0, quote_vol=1.0,
        ))
    await ohlcv_repo.upsert_bars(db, bars)
    trade = _shadow_trade()
    trade.candle_close_time = CANDLE
    trade.opened_at = CANDLE

    features = await build_features_from_shadow_trade(db, trade, make_settings())

    checked = 0
    for key, value in features.items():
        if not key.endswith("_pct") or value is None:
            continue
        checked += 1
        assert abs(value) > _PCT_SANITY_FLOOR, (
            f"{key}={value!r} es sospechosamente chico para este escenario volatil -- "
            "revisa si falta multiplicar por 100 (ver docs/FASE3_6_LLM.md seccion k)"
        )
    assert checked >= 6, "el escenario debe dejar varias features _pct con valor, no null"
