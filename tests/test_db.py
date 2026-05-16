import pytest
import aiosqlite

from app.db import open_db
from app.routes.auth import _hash_password, _verify_password


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


@pytest.mark.asyncio
async def test_exercise_library_seeded():
    conn = await open_db(":memory:")
    try:
        async with conn.execute("SELECT COUNT(*) FROM exercises") as cur:
            row = await cur.fetchone()
        assert row[0] >= 105, f"Expected >= 105 exercises, got {row[0]}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_exercise_metadata_display():
    conn = await open_db(":memory:")
    try:
        async with conn.execute(
            "SELECT category FROM exercises WHERE name = 'Bench Press'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["category"] == "Push"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_seeding_idempotency():
    from app.db import init_db
    conn = await open_db(":memory:")
    try:
        await init_db(conn)
        async with conn.execute(
            "SELECT COUNT(*) FROM routines WHERE user_id IS NULL"
        ) as cur:
            row = await cur.fetchone()
        assert row[0] == 12, f"Expected 12 global routines after double init, got {row[0]}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_columns_exist():
    conn = await open_db(":memory:")
    try:
        async with conn.execute("PRAGMA table_info(exercises)") as cur:
            columns = {r["name"] for r in await cur.fetchall()}
        for col in ("category", "equipment", "cue"):
            assert col in columns, f"Column '{col}' missing from exercises table"
        for col in ("muscle_primary", "muscle_secondary"):
            assert col not in columns, f"Dead column '{col}' should have been dropped"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Admin startup sync helpers (mirrors logic in app/main.py lifespan)
# ---------------------------------------------------------------------------

async def _run_admin_sync(conn, admin_username: str, admin_password: str) -> None:
    """Replicate the admin seeding/sync block from main.py lifespan for testing."""
    async with conn.execute(
        "SELECT id, password_hash, is_admin FROM users WHERE username = ?",
        (admin_username,),
    ) as cur:
        existing = await cur.fetchone()
    if not existing:
        hashed = _hash_password(admin_password)
        await conn.execute(
            "INSERT INTO users(username, password_hash, is_admin) VALUES (?, ?, 1)",
            (admin_username, hashed),
        )
        await conn.commit()
    else:
        password_ok = _verify_password(admin_password, existing["password_hash"])
        already_admin = bool(existing["is_admin"])
        if not password_ok or not already_admin:
            new_hash = (
                _hash_password(admin_password) if not password_ok
                else existing["password_hash"]
            )
            await conn.execute(
                "UPDATE users SET password_hash = ?, is_admin = 1 WHERE id = ?",
                (new_hash, existing["id"]),
            )
            await conn.commit()


@pytest.mark.asyncio
async def test_admin_sync_creates_admin_on_fresh_db():
    conn = await open_db(":memory:")
    try:
        await _run_admin_sync(conn, "admin", "strongpass")
        async with conn.execute(
            "SELECT username, is_admin FROM users WHERE username = 'admin'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert bool(row["is_admin"]) is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_admin_sync_promotes_non_admin_with_matching_password():
    """Existing user with is_admin=0 whose credentials match env vars gets promoted."""
    conn = await open_db(":memory:")
    try:
        hashed = _hash_password("correctpass")
        await conn.execute(
            "INSERT INTO users(username, password_hash, is_admin) VALUES ('admin', ?, 0)",
            (hashed,),
        )
        await conn.commit()

        await _run_admin_sync(conn, "admin", "correctpass")

        async with conn.execute(
            "SELECT is_admin FROM users WHERE username = 'admin'"
        ) as cur:
            row = await cur.fetchone()
        assert bool(row["is_admin"]) is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_admin_sync_updates_password_when_env_var_changes():
    """Admin whose stored hash no longer matches env var gets new hash."""
    conn = await open_db(":memory:")
    try:
        old_hash = _hash_password("oldpass")
        await conn.execute(
            "INSERT INTO users(username, password_hash, is_admin) VALUES ('admin', ?, 1)",
            (old_hash,),
        )
        await conn.commit()

        await _run_admin_sync(conn, "admin", "newpass")

        async with conn.execute(
            "SELECT password_hash, is_admin FROM users WHERE username = 'admin'"
        ) as cur:
            row = await cur.fetchone()
        assert _verify_password("newpass", row["password_hash"])
        assert bool(row["is_admin"]) is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_admin_sync_no_op_when_already_correct():
    """Admin with matching password and is_admin=1 triggers no UPDATE."""
    conn = await open_db(":memory:")
    try:
        hashed = _hash_password("mypass")
        await conn.execute(
            "INSERT INTO users(username, password_hash, is_admin) VALUES ('admin', ?, 1)",
            (hashed,),
        )
        await conn.commit()

        await _run_admin_sync(conn, "admin", "mypass")

        async with conn.execute(
            "SELECT password_hash, is_admin FROM users WHERE username = 'admin'"
        ) as cur:
            row = await cur.fetchone()
        assert _verify_password("mypass", row["password_hash"])
        assert bool(row["is_admin"]) is True
    finally:
        await conn.close()
