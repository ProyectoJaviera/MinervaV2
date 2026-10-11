"""Latido del event loop (incidente de estabilidad 2026-10-11): distinto del
`last_cycle_at` del Scheduler -- ver `app/core/heartbeat.py`."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.heartbeat import Heartbeat


def test_beat_sets_and_returns_last_beat_at():
    hb = Heartbeat()
    assert hb.last_beat_at is None

    now = datetime(2026, 1, 1, tzinfo=UTC)
    returned = hb.beat(now=now)

    assert returned == now
    assert hb.last_beat_at == now


def test_beat_defaults_to_the_current_time_when_not_given(caplog):
    hb = Heartbeat()
    before = datetime.now(UTC)
    with caplog.at_level(logging.INFO, logger="app.core.heartbeat"):
        hb.beat()
    after = datetime.now(UTC)

    assert before <= hb.last_beat_at <= after
    assert any("heartbeat" in record.getMessage() for record in caplog.records)


def test_stop_sets_the_stop_event():
    hb = Heartbeat()
    assert not hb._stop.is_set()
    hb.stop()
    assert hb._stop.is_set()
