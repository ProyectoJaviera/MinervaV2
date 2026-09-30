from __future__ import annotations

import pytest_asyncio

from app.persistence.database import Database


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()
