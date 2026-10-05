"""The coach-chat schema migrations (CM-B).

init_db records a failing migration as applied and never retries it, so a typo in
a new statement would ship silently and stay broken. These tests pin the outcome."""
import aiosqlite
import pytest

import app.db as appdb
from app.db import open_db

# Statements that legitimately error on a FRESH database (they ALTER/backfill things
# the base schema already has); verified against init_db on 2026-10-04. Do not
# add to this set to make a new migration pass: fix the migration.
FROZEN_BENIGN_ERRORS = {0, 10, 38, 48, 49, 51}


def _first_chat_index() -> int:
    return next(i for i, sql in enumerate(appdb._MIGRATIONS) if "coach_messages (" in sql)


async def _error_rows(conn):
    async with conn.execute("SELECT idx, error FROM _schema_migrations WHERE error IS NOT NULL") as c:
        return {r["idx"]: r["error"] for r in await c.fetchall()}


async def _columns(conn, table):
    async with conn.execute(f"PRAGMA table_info({table})") as c:
        return {r["name"]: r for r in await c.fetchall()}


async def _assert_chat_schema(conn):
    async with conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')") as c:
        names = {r["name"] for r in await c.fetchall()}
    assert {"coach_messages", "coach_notes", "coach_usage", "idx_coach_messages_plan"} <= names
    plans = await _columns(conn, "coach_plans")
    assert {"updated_at", "rev", "undo_json"} <= plans.keys()
    assert plans["rev"]["notnull"] == 1 and plans["rev"]["dflt_value"] == "0"
    assert plans["updated_at"]["notnull"] == 0 and plans["undo_json"]["notnull"] == 0
    assert "coach_chat_ack_at" in await _columns(conn, "user_settings")
    assert set(await _columns(conn, "coach_messages")) == {
        "id", "plan_id", "user_id", "role", "content", "changed_days", "changes", "undone", "created_at"}
    assert set(await _columns(conn, "coach_notes")) == {"id", "user_id", "text", "source_plan_id", "created_at"}
    assert set(await _columns(conn, "coach_usage")) == {"day", "count"}


@pytest.mark.asyncio
async def test_fresh_database_has_only_the_frozen_benign_errors_and_the_chat_schema():
    conn = await open_db(":memory:")
    try:
        errors = await _error_rows(conn)
        assert set(errors) == FROZEN_BENIGN_ERRORS, errors
        first = _first_chat_index()
        assert first == 52 and not [i for i in errors if i >= first]
        async with conn.execute("SELECT COUNT(*) FROM _schema_migrations") as c:
            assert (await c.fetchone())[0] == len(appdb._MIGRATIONS)
        await _assert_chat_schema(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_pre_chat_database_upgrades_cleanly_and_keeps_its_rows():
    """Simulate a production DB that predates the chat: no chat tables or columns, no
    migration rows from the first chat index on, and an existing saved plan."""
    conn = await open_db(":memory:")
    try:
        first = _first_chat_index()
        await conn.execute("INSERT INTO users(id, username, password_hash, is_admin) VALUES (7, 'u', 'x', 0)")
        for table in ("coach_messages", "coach_notes", "coach_usage"):
            await conn.execute(f"DROP TABLE {table}")
        for col in ("updated_at", "rev", "undo_json"):
            await conn.execute(f"ALTER TABLE coach_plans DROP COLUMN {col}")
        await conn.execute("ALTER TABLE user_settings DROP COLUMN coach_chat_ack_at")
        await conn.execute("DELETE FROM _schema_migrations WHERE idx >= ?", (first,))
        await conn.execute(
            "INSERT INTO coach_plans(id, user_id, title, goal, days_per_week, plan_json, status) "
            "VALUES (5, 7, 'Old plan', 'strength', 3, '{}', 'saved')")
        assert "rev" not in await _columns(conn, "coach_plans")

        await appdb.init_db(conn)   # what the next deploy does

        errors = await _error_rows(conn)
        assert set(errors) == FROZEN_BENIGN_ERRORS, errors
        await _assert_chat_schema(conn)
        async with conn.execute("SELECT title, rev, updated_at, undo_json FROM coach_plans WHERE id=5") as c:
            row = await c.fetchone()
        assert (row["title"], row["rev"], row["updated_at"], row["undo_json"]) == ("Old plan", 0, None, None)

        await appdb.init_db(conn)   # and a second start is a no-op
        assert set(await _error_rows(conn)) == FROZEN_BENIGN_ERRORS
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_chat_table_constraints_and_cascades():
    conn = await open_db(":memory:")
    try:
        await conn.execute("INSERT INTO users(id, username, password_hash, is_admin) VALUES (1, 'a', 'x', 0)")
        await conn.execute(
            "INSERT INTO coach_plans(id, user_id, title, goal, days_per_week, plan_json, status) "
            "VALUES (1, 1, 'P', 'strength', 3, '{}', 'draft')")
        await conn.execute("INSERT INTO coach_messages(plan_id, user_id, role, content) VALUES (1, 1, 'user', 'hi')")
        with pytest.raises(aiosqlite.IntegrityError):                       # role is user|model only
            await conn.execute("INSERT INTO coach_messages(plan_id, user_id, role, content) VALUES (1, 1, 'event', 'x')")
        await conn.execute("INSERT INTO coach_notes(user_id, text, source_plan_id) VALUES (1, 'Left knee clicks', 1)")
        with pytest.raises(aiosqlite.IntegrityError):                       # case-insensitive unique per user
            await conn.execute("INSERT INTO coach_notes(user_id, text) VALUES (1, 'LEFT KNEE CLICKS')")
        await conn.execute("INSERT INTO users(id, username, password_hash, is_admin) VALUES (2, 'b', 'x', 0)")
        await conn.execute("INSERT INTO coach_notes(user_id, text) VALUES (2, 'left knee clicks')")   # other user: fine

        await conn.execute("DELETE FROM coach_plans WHERE id = 1")
        async with conn.execute("SELECT COUNT(*) FROM coach_messages") as c:
            assert (await c.fetchone())[0] == 0                              # messages cascade with the plan
        async with conn.execute("SELECT source_plan_id FROM coach_notes WHERE user_id = 1") as c:
            assert (await c.fetchone())[0] is None                           # notes outlive the plan
        await conn.execute("DELETE FROM users WHERE id = 1")
        async with conn.execute("SELECT COUNT(*) FROM coach_notes WHERE user_id = 1") as c:
            assert (await c.fetchone())[0] == 0                              # notes cascade with the user
    finally:
        await conn.close()
