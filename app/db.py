import asyncio
import contextlib
import contextvars
import os as _os
import re
import sys as _sys

# SQLCipher: replace sqlite3 with pysqlcipher3 before aiosqlite is imported.
# PRAGMA key must be the first command after connect — see open_db().
_DB_ENCRYPTION_KEY = _os.environ.get("DB_ENCRYPTION_KEY", "").strip()
if _DB_ENCRYPTION_KEY:
    try:
        import sqlcipher3.dbapi2 as _sqlcipher3  # noqa: F401
        _sys.modules.setdefault("sqlite3", _sqlcipher3)
    except ImportError:
        import logging as _early_log
        _early_log.basicConfig()
        _early_log.getLogger(__name__).warning(
            "DB_ENCRYPTION_KEY is set but sqlcipher3 is not installed — "
            "database will run UNENCRYPTED. "
            "Install: pip install sqlcipher3"
        )
        _DB_ENCRYPTION_KEY = ""

import logging
import aiosqlite
from aiosqlite.context import Result
from pathlib import Path

from app.data.exercises import EXERCISES, RETIRED_EXERCISES, infer_muscle_and_category
from app.data.routines import ROUTINES
from app.utils.pr_utils import epley

_conn: aiosqlite.Connection | None = None

# The single writer's lock for the shared connection. write_tx() holds it for
# a whole transaction, and every other write statement takes it for its one
# statement (see _gate_writes), so no write ever runs inside someone else's
# open transaction. Only write_tx opens transactions (a test enforces it).
# One global lock, not per-table/per-route: this app's real concurrency is
# low enough that serializing writes is the right amount of locking.
write_lock = asyncio.Lock()

# True while the current task is inside write_tx(). write_tx takes write_lock,
# which is not reentrant, so nested use would deadlock; the flag turns that
# into an immediate error instead.
_in_write_tx: contextvars.ContextVar[bool] = contextvars.ContextVar("in_write_tx", default=False)


class WriteConflict(Exception):
    """Raised inside write_tx() when a compare-and-set guard finds the row changed.

    write_tx COMMITs (nothing was written, the guard runs first) and re-raises;
    app.main turns it into a 409 response carrying `kind` so a client can tell, say,
    a stale tab from a replaced plan.
    """

    def __init__(self, message: str = "", kind: str = "conflict"):
        super().__init__(message)
        self.kind = kind


@contextlib.asynccontextmanager
async def write_tx(conn: aiosqlite.Connection):
    """One atomic write: write_lock + BEGIN IMMEDIATE ... COMMIT.

    Rules for the body: database statements only (never await the network or
    sleep while holding the app's single write lock), and put any
    compare-and-set UPDATE first, raising WriteConflict when it matches no row.
    Any other exception rolls the whole transaction back.
    """
    if _in_write_tx.get():
        raise RuntimeError("nested write_tx")
    async with write_lock:  # module global, so clear_db()'s fresh lock is seen
        token = _in_write_tx.set(True)
        try:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                await conn.execute("COMMIT")
            except WriteConflict:
                await conn.execute("COMMIT")
                raise
            except BaseException:
                try:
                    await conn.execute("ROLLBACK")
                except Exception:
                    logging.warning("write_tx: ROLLBACK failed", exc_info=True)
                raise
        finally:
            _in_write_tx.reset(token)


async def _commit_is_a_noop() -> None:
    return None


# Statements that only read: they run at once, even while another request's
# transaction is open. PRAGMA only tunes or inspects the connection.
_READ_ONLY = re.compile(r"^\s*(SELECT|EXPLAIN|PRAGMA)\b", re.IGNORECASE)
_WITH_WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def _is_write(sql: str) -> bool:
    if _READ_ONLY.match(sql):
        return False
    if re.match(r"^\s*WITH\b", sql, re.IGNORECASE):
        return bool(_WITH_WRITE.search(sql))
    return True


def _gate_writes(conn: aiosqlite.Connection) -> None:
    """Make every write outside write_tx wait for write_lock.

    The connection is shared and autocommit, so a write issued while another
    request's write_tx is open would run inside that transaction and vanish if
    it rolled back. Taking the lock queues the write until the transaction
    ends. Inside write_tx the lock is already held, so statements pass through.
    """
    execute, executemany, executescript = conn.execute, conn.executemany, conn.executescript

    async def _gated(run, sql):
        if _in_write_tx.get() or not _is_write(sql):
            return await run()
        async with write_lock:   # module global: clear_db() replaces it
            return await run()

    def gated_execute(sql, parameters=None):
        return Result(_gated(lambda: execute(sql, parameters), sql))

    def gated_executemany(sql, parameters):
        return Result(_gated(lambda: executemany(sql, parameters), sql))

    def gated_executescript(sql_script):
        return Result(_gated(lambda: executescript(sql_script), "BEGIN"))

    conn.execute, conn.executemany, conn.executescript = gated_execute, gated_executemany, gated_executescript


SCHEMA = Path(__file__).parent.parent / "schema.sql"


_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN email TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL",
    """CREATE TABLE IF NOT EXISTS password_reset_tokens (
        token      TEXT     PRIMARY KEY,
        user_id    INTEGER  NOT NULL REFERENCES users(id),
        created_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
        expires_at DATETIME NOT NULL,
        used_at    DATETIME NULL
    )""",
    "ALTER TABLE exercises ADD COLUMN category TEXT",
    "ALTER TABLE exercises ADD COLUMN equipment TEXT",
    "ALTER TABLE exercises ADD COLUMN muscle_primary TEXT",
    "ALTER TABLE exercises ADD COLUMN muscle_secondary TEXT",
    "ALTER TABLE exercises ADD COLUMN cue TEXT",
    "ALTER TABLE exercises DROP COLUMN muscle_primary",
    "ALTER TABLE exercises DROP COLUMN muscle_secondary",
    "ALTER TABLE sets ADD COLUMN rpe INTEGER",
    """CREATE TABLE IF NOT EXISTS cardio_logs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          INTEGER NOT NULL REFERENCES users(id),
        exercise_id      INTEGER REFERENCES exercises(id),
        logged_date      TEXT    NOT NULL DEFAULT (date('now','localtime')),
        duration_minutes REAL    NOT NULL,
        distance_km      REAL,
        notes            TEXT,
        created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "ALTER TABLE cardio_logs ADD COLUMN workout_id INTEGER REFERENCES workouts(id)",
    "ALTER TABLE body_metrics ADD COLUMN notes TEXT",
    "ALTER TABLE body_metrics ADD COLUMN entry_date TEXT",
    """CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        weekly_goal_sessions INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS exercise_goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id),
        target_kg REAL NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, exercise_id)
    )""",
    """CREATE TABLE IF NOT EXISTS workout_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS workout_template_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id INTEGER NOT NULL REFERENCES workout_templates(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id),
        order_idx INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS body_measurements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        logged_date TEXT    NOT NULL DEFAULT (date('now','localtime')),
        site        TEXT    NOT NULL,
        value_cm    REAL    NOT NULL,
        notes       TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_bm_user_site ON body_measurements(user_id, site, logged_date)",
    """CREATE TABLE IF NOT EXISTS user_achievements (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        achievement_id TEXT NOT NULL,
        unlocked_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(user_id, achievement_id)
    )""",
    """CREATE TABLE IF NOT EXISTS mesocycle_plans (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        goal        TEXT NOT NULL,
        weeks       INTEGER NOT NULL,
        plan_json   TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS workout_enrichments (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workout_id   INTEGER NOT NULL UNIQUE REFERENCES workouts(id) ON DELETE CASCADE,
        form_info    TEXT    NOT NULL DEFAULT '{}',
        generated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "ALTER TABLE user_settings ADD COLUMN pref_unit TEXT NOT NULL DEFAULT 'kg'",
    """CREATE TABLE IF NOT EXISTS coach_plans (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title         TEXT    NOT NULL,
        goal          TEXT    NOT NULL,
        days_per_week INTEGER NOT NULL,
        plan_json     TEXT    NOT NULL,
        model         TEXT,
        created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_coach_plans_user ON coach_plans(user_id, created_at)",
    """CREATE TABLE IF NOT EXISTS deleted_items (
        token      TEXT    PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind       TEXT    NOT NULL,
        label      TEXT    NOT NULL,
        payload    TEXT    NOT NULL,
        deleted_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_deleted_items_user ON deleted_items(user_id, deleted_at)",
    """CREATE TABLE IF NOT EXISTS challenge_attempts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        template_key TEXT    NOT NULL,
        title        TEXT    NOT NULL,
        total_days   INTEGER NOT NULL,
        status       TEXT    NOT NULL DEFAULT 'active',
        started_on   TEXT    NOT NULL DEFAULT (date('now','localtime')),
        ended_on     TEXT,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_challenge_attempts_user ON challenge_attempts(user_id, status)",
    """CREATE TABLE IF NOT EXISTS challenge_checkins (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL REFERENCES challenge_attempts(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day_date   TEXT    NOT NULL,
        rules_json TEXT    NOT NULL DEFAULT '{}',
        updated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(attempt_id, day_date)
    )""",
    # Custom per-attempt rules for editable challenges (75 Medium).
    # NULL means "use template defaults"; JSON array means user-defined rules.
    "ALTER TABLE challenge_attempts ADD COLUMN rules_json TEXT NULL",
    # Server-side session registry for revocable logins.
    # Logout deletes the row; middleware rejects cookies whose sid is absent.
    """CREATE TABLE IF NOT EXISTS sessions (
        id         TEXT    PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    "ALTER TABLE user_settings ADD COLUMN pref_distance TEXT NOT NULL DEFAULT 'km'",
    """CREATE TABLE IF NOT EXISTS daily_logs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        log_date     TEXT    NOT NULL,
        day_number   INTEGER,
        weight_kg    REAL,
        workout      TEXT,
        meal_1       TEXT,
        meal_2       TEXT,
        meal_3       TEXT,
        water_l      REAL,
        energy       TEXT CHECK(energy IN ('low','medium','high')),
        motivation   TEXT CHECK(motivation IN ('low','medium','high')),
        sleep_hrs    REAL,
        steps        INTEGER,
        notes        TEXT,
        created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(user_id, log_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_daily_logs_user ON daily_logs(user_id, log_date)",
    # REPAIR: pref_distance was first added mid-list (index 35), which shifted the
    # daily_logs migrations and caused the live DB to skip it (its slot was already
    # marked applied by the old daily_logs-table migration). Re-add it as a fresh
    # tail entry so it runs on databases that missed it. Harmless on fresh installs
    # (the runner swallows the "duplicate column" error). Migrations are APPEND-ONLY
    # — never insert in the middle again.
    "ALTER TABLE user_settings ADD COLUMN pref_distance TEXT NOT NULL DEFAULT 'km'",
    # Bodyweight sets: weight_kg stores the EFFECTIVE load (bodyweight + added) so
    # volume math is unchanged; added_weight_kg (NULL = a normal weighted set)
    # records the extra plate/belt weight and flags the set as bodyweight for
    # display ("BW +20kg" vs a bare number).
    "ALTER TABLE sets ADD COLUMN added_weight_kg REAL",
    # Timed-hold exercises (planks): log_type='time' on the exercise; a set then
    # stores duration_seconds and reps=0 (so it stays out of the kg-volume sum,
    # which is weight×reps). Holds progress by time, not volume.
    "ALTER TABLE exercises ADD COLUMN log_type TEXT",
    "ALTER TABLE sets ADD COLUMN duration_seconds INTEGER",
    # Body measurement unit preference (cm / in) — separate from pref_unit (weight)
    "ALTER TABLE user_settings ADD COLUMN pref_body_measurement TEXT NOT NULL DEFAULT 'cm'",
    # Post-plan feedback — too_easy | just_right | too_hard | skipped_often | NULL
    "ALTER TABLE coach_plans ADD COLUMN feedback TEXT",
    # Achievement toast notifications — 0 = unseen, 1 = shown
    "ALTER TABLE user_achievements ADD COLUMN seen INTEGER NOT NULL DEFAULT 0",
    # Remove orphaned coach-created routines (from deleted plans). The coach names
    # routines "<title> · Day N: <focus>"; that middle-dot pattern is unique to
    # generated plans. Only deletes routines NOT referenced by any active coach plan.
    """DELETE FROM routines
       WHERE user_id IS NOT NULL
         AND name LIKE '% · Day %'
         AND id NOT IN (
             SELECT CAST(value AS INTEGER)
             FROM coach_plans, json_each(json_extract(plan_json, '$.routine_ids'))
             WHERE json_extract(plan_json, '$.routine_ids') IS NOT NULL
         )""",
    # idx=46 slot was consumed by a second pass of the orphaned-routines cleanup
    # after an index-shift incident. This no-op placeholder preserves index parity
    # so idx=47 is the true new migration on already-migrated databases.
    "SELECT 1",
    # Coach plan lifecycle: 'draft' = generated but not yet confirmed by user;
    # 'saved' = confirmed, routines created. Default keeps existing rows as saved.
    "ALTER TABLE coach_plans ADD COLUMN status TEXT NOT NULL DEFAULT 'saved'",
    # Multi-use invite links: an invite is valid while uses_count < max_uses.
    # DEFAULT 1 means pre-existing invite rows keep their original
    # one-time-use behavior after this migration runs.
    "ALTER TABLE invite_tokens ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE invite_tokens ADD COLUMN uses_count INTEGER NOT NULL DEFAULT 0",
    # Backfill: any invite already consumed under the old one-time-use scheme
    # (used_at set) must not become usable again just because uses_count
    # defaulted to 0. Mark it as already at its cap.
    "UPDATE invite_tokens SET uses_count = max_uses WHERE used_at IS NOT NULL",
    # used_by named a single user; multi-use invites can be used by several,
    # so a single FK column no longer makes sense. No audit trail replaces it
    # (see docs/superpowers/specs/2026-07-12-multi-use-invite-links-design.md).
    "ALTER TABLE invite_tokens DROP COLUMN used_by",
    # ── Coach chat (docs/designs/coach-chat.md). Append-only: one statement per entry. ──
    """CREATE TABLE IF NOT EXISTS coach_messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id      INTEGER NOT NULL REFERENCES coach_plans(id) ON DELETE CASCADE,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role         TEXT    NOT NULL CHECK(role IN ('user','model')),
        content      TEXT    NOT NULL,
        changed_days TEXT,
        changes      TEXT,
        undone       INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_coach_messages_plan ON coach_messages(plan_id, id)",
    """CREATE TABLE IF NOT EXISTS coach_notes (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text           TEXT    NOT NULL,
        source_plan_id INTEGER REFERENCES coach_plans(id) ON DELETE SET NULL,
        created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(user_id, text COLLATE NOCASE)
    )""",
    # updated_at: last activity, used ONLY by the 7-day draft purge.
    "ALTER TABLE coach_plans ADD COLUMN updated_at TEXT",
    # rev: the compare-and-set token for stale-tab detection (a nullable timestamp
    # would never match: NULL = ? is never true).
    "ALTER TABLE coach_plans ADD COLUMN rev INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE coach_plans ADD COLUMN undo_json TEXT",
    # One row per America/Los_Angeles day: how many Gemini requests the coach made.
    """CREATE TABLE IF NOT EXISTS coach_usage (
        day   TEXT    PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    )""",
    "ALTER TABLE user_settings ADD COLUMN coach_chat_ack_at TEXT",
]


def _already_applied(error: str) -> bool:
    """True when a migration's error means its change is already in the schema."""
    msg = error.lower()
    return any(s in msg for s in ("duplicate column", "no such column", "already exists"))


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA.read_text())

    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_migrations "
        "(idx INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, error TEXT)"
    )
    # A recorded failure counts as applied only when it says the change is
    # already there; anything else (e.g. "database is locked") is retried on
    # every boot until it succeeds.
    async with conn.execute("SELECT idx, error FROM _schema_migrations") as _cur:
        _applied = {row[0] for row in await _cur.fetchall()
                    if row[1] is None or _already_applied(row[1])}

    for idx, sql in enumerate(_MIGRATIONS):
        if idx in _applied:
            continue
        try:
            await conn.execute(sql)
            await conn.execute(
                "INSERT OR REPLACE INTO _schema_migrations(idx, applied_at) VALUES (?, datetime('now','localtime'))",
                (idx,),
            )
        except Exception as exc:
            if _already_applied(str(exc)):
                logging.debug("Migration %d already applied: %s", idx, exc)
            else:
                logging.warning("Migration %d failed (retried next start): %s | sql: %.120s", idx, exc, sql)
            await conn.execute(
                "INSERT OR REPLACE INTO _schema_migrations(idx, applied_at, error) VALUES (?, datetime('now','localtime'), ?)",
                (idx, str(exc)),
            )

    # OV3: validate all routine exercise names exist before any DB writes
    exercise_names = {ex["name"] for ex in EXERCISES}
    for routine in ROUTINES:
        for ex_name in routine["exercises"]:
            if ex_name not in exercise_names:
                raise ValueError(
                    f"Routine '{routine['name']}' references unknown exercise '{ex_name}'"
                )

    await conn.execute("BEGIN IMMEDIATE")
    for ex in EXERCISES:
        await conn.execute(
            "INSERT OR IGNORE INTO exercises(name) VALUES (?)", (ex["name"],)
        )
        await conn.execute(
            """UPDATE exercises
               SET category=?, equipment=?, cue=?, log_type=?
               WHERE name=?""",
            (
                ex["category"],
                ex["equipment"],
                ex["cue"],
                ex.get("log_type", "reps"),
                ex["name"],
            ),
        )
    for ex in EXERCISES:
        async with conn.execute("SELECT id FROM exercises WHERE name=?", (ex["name"],)) as _cur:
            row = await _cur.fetchone()
        if row:
            ex_id = row["id"]
            muscles = []
            for m in (ex.get("muscle_primary") or "").split(", "):
                m = m.strip()
                if m:
                    muscles.append((ex_id, m, 1))
            for m in (ex.get("muscle_secondary") or "").split(", "):
                m = m.strip()
                if m:
                    muscles.append((ex_id, m, 0))
            seen: dict = {}
            for (eid, muscle, is_p) in muscles:
                if muscle not in seen:
                    seen[muscle] = is_p
            await conn.execute(
                "DELETE FROM exercise_muscles WHERE exercise_id = ?", (ex_id,)
            )
            for muscle, is_p in seen.items():
                await conn.execute(
                    "INSERT INTO exercise_muscles(exercise_id, muscle, is_primary) VALUES (?,?,?)",
                    (ex_id, muscle, is_p),
                )

    await conn.execute("DELETE FROM routines WHERE user_id IS NULL")
    for routine in ROUTINES:
        cur = await conn.execute(
            "INSERT INTO routines(name, user_id) VALUES (?, NULL)", (routine["name"],)
        )
        routine_id = cur.lastrowid
        for idx, ex_name in enumerate(routine["exercises"]):
            await conn.execute(
                """INSERT OR IGNORE INTO routine_exercises(routine_id, exercise_id, order_idx)
                   SELECT ?, id, ? FROM exercises WHERE name=?""",
                (routine_id, idx, ex_name),
            )

    # Retire deprecated junk exercises — but ONLY when nothing references them.
    # Runs after the global routines are re-seeded above (so their stale rows are
    # gone first). Any exercise a user actually trained (sets), or that lives in a
    # user routine / template / cardio log / goal, is left untouched.
    for name in RETIRED_EXERCISES:
        async with conn.execute("SELECT id FROM exercises WHERE name=?", (name,)) as _c:
            _row = await _c.fetchone()
        if not _row:
            continue
        eid = _row["id"]
        async with conn.execute(
            "SELECT (SELECT COUNT(*) FROM sets WHERE exercise_id=:e)"
            "     + (SELECT COUNT(*) FROM routine_exercises WHERE exercise_id=:e)"
            "     + (SELECT COUNT(*) FROM workout_template_exercises WHERE exercise_id=:e)"
            "     + (SELECT COUNT(*) FROM cardio_logs WHERE exercise_id=:e)"
            "     + (SELECT COUNT(*) FROM exercise_goals WHERE exercise_id=:e) AS refs",
            {"e": eid},
        ) as _c:
            refs = (await _c.fetchone())["refs"]
        if refs == 0:
            await conn.execute("DELETE FROM exercise_muscles WHERE exercise_id=?", (eid,))
            await conn.execute("DELETE FROM exercises WHERE id=?", (eid,))

    # Backfill: any exercise with no muscle group (e.g. older custom exercises
    # created via the in-workout quick-add before this) gets one inferred from its
    # name, so it shows up in the muscle filter and stats coverage.
    async with conn.execute(
        """SELECT e.id, e.name, e.category FROM exercises e
           WHERE NOT EXISTS (SELECT 1 FROM exercise_muscles em WHERE em.exercise_id = e.id)"""
    ) as _c:
        _orphans = [dict(r) for r in await _c.fetchall()]
    for ex in _orphans:
        muscle, category = infer_muscle_and_category(ex["name"])
        if not ex.get("category") and category:
            await conn.execute("UPDATE exercises SET category=? WHERE id=?", (category, ex["id"]))
        if muscle:
            await conn.execute(
                "INSERT INTO exercise_muscles(exercise_id, muscle, is_primary) VALUES (?, ?, 1)",
                (ex["id"], muscle),
            )

    await conn.execute("COMMIT")
    logging.info("Seeded %d exercises, %d global routines", len(EXERCISES), len(ROUTINES))


async def open_db(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    # The connection is shared by every request and runs in autocommit mode
    # (isolation_level=None). A stray `await conn.commit()` from one request
    # would therefore COMMIT another request's open BEGIN IMMEDIATE, and its
    # later ROLLBACK would fail with the partial writes already persisted.
    # Transactions end only with an explicit execute("COMMIT"/"ROLLBACK")
    # (write_tx and the existing BEGIN IMMEDIATE sites do), so commit() is
    # made a no-op; outside a transaction each statement already autocommits.
    conn.commit = _commit_is_a_noop
    _gate_writes(conn)
    await conn.create_function("e1rm", 2, epley, deterministic=True)
    if _DB_ENCRYPTION_KEY:
        # SQLCipher: must precede every other query, including PRAGMAs.
        # Hex-blob format avoids injection: x'<64 lowercase hex chars>'
        await conn.execute(f"PRAGMA key=\"x'{_DB_ENCRYPTION_KEY}'\"")
        try:
            await conn.execute("SELECT count(*) FROM sqlite_master")
        except Exception as exc:
            await conn.close()
            raise RuntimeError(
                "DB_ENCRYPTION_KEY is set but the database could not be opened. "
                "Either the key is wrong or the database is not yet encrypted. "
                "Run: python scripts/encrypt_db.py"
            ) from exc
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await init_db(conn)
    return conn


async def get_db() -> aiosqlite.Connection:
    """FastAPI dependency — yields the shared connection."""
    assert _conn is not None, "DB not initialised; call set_db() from lifespan"
    yield _conn


def set_db(conn: aiosqlite.Connection) -> None:
    global _conn
    _conn = conn


def clear_db() -> None:
    # Recreating write_lock (not just clearing it) matters for tests: each
    # pytest-asyncio test gets its own event loop, and an asyncio.Lock binds
    # to whichever loop first awaits it — reusing one Lock across tests would
    # raise "attached to a different loop". Routes reference this via
    # `app.db.write_lock` (not `from app.db import write_lock`) specifically
    # so this reassignment is visible to them. Also runs once at production
    # shutdown (see main.py's lifespan) — harmless there, since nothing
    # acquires the lock again after the connection is closed.
    global _conn, write_lock
    _conn = None
    write_lock = asyncio.Lock()
