"""Plantilla del prompt (subfase 3.6, fase iii-A): JSON crudo exigido, hash
estable, features desde datos ya guardados, con nulos explicitos."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from app.llm.prompts import (
    PROMPT_SHA256,
    SYSTEM_PROMPT,
    build_features_from_shadow_trade,
    build_user_message,
    estimate_input_tokens,
)
from app.persistence.models import ShadowTrade, Side, SignalRecord, TradeStatus
from app.persistence.repositories import signals_repo

CANDLE = datetime(2026, 1, 1, 4, tzinfo=UTC)


def test_system_prompt_forbids_markdown_code_blocks():
    assert "```" in SYSTEM_PROMPT  # lo menciona para prohibirlo explicitamente
    assert "sin bloques de código" in SYSTEM_PROMPT or "sin texto adicional" in SYSTEM_PROMPT


def test_prompt_sha256_matches_the_system_prompt():
    assert PROMPT_SHA256 == hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def test_build_user_message_is_valid_sorted_json():
    text = build_user_message({"b": 1, "a": None})
    assert text == '{"a": null, "b": 1}'
    assert json.loads(text) == {"a": None, "b": 1}


def test_estimate_input_tokens_is_chars_over_three():
    assert estimate_input_tokens("abc", "defghi") == 3  # (3+6)//3


def _shadow_trade(key="BTCUSDT|LONG|c1", strategies=("ema_cross_9_21",)) -> ShadowTrade:
    trade = ShadowTrade(
        symbol="BTCUSDT", side=Side.LONG, strategy=strategies[0], status=TradeStatus.OPEN,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0,
        opened_at=CANDLE, contributing_strategies=list(strategies), signal_group_key=key,
        candle_close_time=CANDLE, sl_margin_loss_pct=50.0,
    )
    return trade


@pytest.mark.asyncio
async def test_features_include_indicators_from_an_existing_signal_row(db):
    await signals_repo.insert_signal(db, SignalRecord(
        symbol="BTCUSDT", strategy="ema_cross_9_21", timeframe="1h",
        candle_close_time=CANDLE, evaluated_at=CANDLE, signal="LONG", price_at_eval=100.0,
        indicators_json=json.dumps({"ema_fast": 101.2, "ema_slow": 99.8}),
    ))
    trade = _shadow_trade()

    features = await build_features_from_shadow_trade(db, trade)

    assert features["indicators_by_strategy"]["ema_cross_9_21"] == {
        "ema_fast": 101.2, "ema_slow": 99.8,
    }
    assert features["symbol"] == "BTCUSDT" and features["side"] == "LONG"
    assert features["sl_margin_loss_pct"] == 50.0


@pytest.mark.asyncio
async def test_features_are_null_explicit_when_a_signal_row_is_missing(db):
    trade = _shadow_trade(strategies=("ema_cross_9_21", "donchian_breakout_20"))

    features = await build_features_from_shadow_trade(db, trade)

    assert features["indicators_by_strategy"] == {
        "ema_cross_9_21": None, "donchian_breakout_20": None,
    }
    assert features["funding_rate_pct"] is None
    assert features["open_real_positions"] is None


@pytest.mark.asyncio
async def test_features_json_round_trips_through_build_user_message(db):
    trade = _shadow_trade()
    features = await build_features_from_shadow_trade(db, trade)
    text = build_user_message(features)
    assert json.loads(text) == features
