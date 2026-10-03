"""Migraciones idempotentes sobre una base con el esquema ANTERIOR a 3.3/3.4:
`CREATE TABLE IF NOT EXISTS` no agrega columnas nuevas a una tabla existente,
asi que `minerva.db` de una corrida previa depende de `_migrate_columns`."""

from __future__ import annotations

import aiosqlite
import pytest

from app.persistence.database import Database


@pytest.mark.asyncio
async def test_old_schema_gets_new_trade_and_signal_columns(tmp_path):
    db_path = str(tmp_path / "old_schema.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            side TEXT NOT NULL, strategy TEXT, status TEXT NOT NULL DEFAULT 'OPEN',
            leverage INTEGER NOT NULL, margin_usdt REAL NOT NULL, notional_usdt REAL NOT NULL,
            qty REAL NOT NULL, entry_price REAL NOT NULL, exit_price REAL,
            fee_entry_usdt REAL NOT NULL DEFAULT 0, fee_exit_usdt REAL,
            funding_paid_usdt REAL NOT NULL DEFAULT 0, pnl_gross_usdt REAL, pnl_net_usdt REAL,
            close_reason TEXT, opened_at TEXT NOT NULL, closed_at TEXT, decision_json TEXT)"""
        )
        await conn.execute(
            """INSERT INTO trades (symbol, side, leverage, margin_usdt, notional_usdt, qty,
            entry_price, opened_at) VALUES ('BTCUSDT', 'LONG', 10, 10, 100, 1, 100,
            '2026-01-01T00:00:00+00:00')"""
        )
        await conn.execute(
            """CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            strategy TEXT NOT NULL, timeframe TEXT NOT NULL, candle_close_time TEXT NOT NULL,
            signal TEXT NOT NULL, price_at_eval REAL NOT NULL, evaluated_at TEXT NOT NULL,
            status TEXT, UNIQUE (symbol, strategy, timeframe, candle_close_time))"""
        )
        await conn.commit()

    db = Database(db_path)
    await db.connect()
    try:
        cursor = await db.conn.execute("PRAGMA table_info(trades)")
        trade_cols = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        cursor = await db.conn.execute("PRAGMA table_info(signals)")
        signal_cols = {row[1] for row in await cursor.fetchall()}
        await cursor.close()

        assert {"sl_price", "tp_price", "effective_stop", "liq_price",
                "funding_last_applied_ms", "decision_source"} <= trade_cols
        assert "reason" in signal_cols
        # La fila anterior sobrevive y las columnas nuevas quedan NULL.
        row = await db.fetch_one("SELECT sl_price, decision_source FROM trades")
        assert row["sl_price"] is None and row["decision_source"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_migrations_are_idempotent_on_reconnect(tmp_path):
    db_path = str(tmp_path / "reconnect.db")
    for _ in range(2):
        db = Database(db_path)
        await db.connect()
        await db.close()
