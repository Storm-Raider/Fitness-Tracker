"""A migration that failed for a passing reason must be retried on the next boot.

init_db records every failure in _schema_migrations. Before this fix it then
treated that row as applied forever, so a transient error ("database is
locked") silently skipped a schema change for good. Harmless failures
("duplicate column", "already exists") still count as applied.
"""
import pytest

import app.db
from app.db import init_db, open_db


async def _row(conn, idx):
    async with conn.execute("SELECT error FROM _schema_migrations WHERE idx = ?", (idx,)) as cur:
        return await cur.fetchone()


async def _table_exists(conn, name):
    async with conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) as cur:
        return await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_failed_migration_is_retried_and_its_error_cleared(monkeypatch):
    conn = await open_db(":memory:")
    try:
        idx = len(app.db._MIGRATIONS)
        monkeypatch.setattr(app.db, "_MIGRATIONS", app.db._MIGRATIONS + ["CREATE TABLE retry_probe (x INTEGER)"])
        await conn.execute(
            "INSERT INTO _schema_migrations(idx, applied_at, error) VALUES (?, datetime('now'), 'database is locked')",
            (idx,),
        )
        await conn.commit()

        await init_db(conn)

        assert await _table_exists(conn, "retry_probe")
        assert (await _row(conn, idx))["error"] is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_harmless_failure_still_counts_as_applied(monkeypatch):
    conn = await open_db(":memory:")
    try:
        idx = len(app.db._MIGRATIONS)
        monkeypatch.setattr(app.db, "_MIGRATIONS", app.db._MIGRATIONS + ["CREATE TABLE benign_probe (x INTEGER)"])
        await conn.execute(
            "INSERT INTO _schema_migrations(idx, applied_at, error) "
            "VALUES (?, datetime('now'), 'duplicate column name: email')",
            (idx,),
        )
        await conn.commit()

        await init_db(conn)

        assert not await _table_exists(conn, "benign_probe")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_migration_that_keeps_failing_keeps_its_latest_error(monkeypatch):
    conn = await open_db(":memory:")
    try:
        idx = len(app.db._MIGRATIONS)
        monkeypatch.setattr(app.db, "_MIGRATIONS", app.db._MIGRATIONS + ["ALTER TABLE no_such_table_xyz ADD COLUMN y"])
        await conn.execute(
            "INSERT INTO _schema_migrations(idx, applied_at, error) VALUES (?, datetime('now'), 'database is locked')",
            (idx,),
        )
        await conn.commit()

        await init_db(conn)

        assert "no such table" in (await _row(conn, idx))["error"]
    finally:
        await conn.close()
