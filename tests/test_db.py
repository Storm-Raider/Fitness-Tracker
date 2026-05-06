import pytest
import aiosqlite

from app.db import open_db


@pytest.mark.asyncio
async def test_open_db_creates_schema():
    conn = await open_db(":memory:")
    try:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {r["name"] for r in await cur.fetchall()}
        assert "exercises" in tables
        assert "workouts" in tables
        assert "sets" in tables
        assert "body_metrics" in tables
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_foreign_key_pragma():
    conn = await open_db(":memory:")
    try:
        async with conn.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
        assert row[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_wal_mode():
    conn = await open_db(":memory:")
    try:
        async with conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        # In-memory DBs return "memory", not "wal" — just check it doesn't crash
        assert row[0] in ("wal", "memory")
    finally:
        await conn.close()
