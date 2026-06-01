import logging
import aiosqlite
from pathlib import Path

from app.data.exercises import EXERCISES
from app.data.routines import ROUTINES

_conn: aiosqlite.Connection | None = None

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
]


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA.read_text())

    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_migrations "
        "(idx INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, error TEXT)"
    )
    async with conn.execute("SELECT idx FROM _schema_migrations") as _cur:
        _applied = {row[0] for row in await _cur.fetchall()}

    for idx, sql in enumerate(_MIGRATIONS):
        if idx in _applied:
            continue
        try:
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _schema_migrations(idx, applied_at) VALUES (?, datetime('now','localtime'))",
                (idx,),
            )
        except Exception as exc:
            msg = str(exc).lower()
            if any(s in msg for s in ("duplicate column", "no such column", "already exists")):
                logging.debug("Migration %d already applied: %s", idx, exc)
            else:
                logging.warning("Migration %d failed: %s | sql: %.120s", idx, exc, sql)
            await conn.execute(
                "INSERT OR IGNORE INTO _schema_migrations(idx, applied_at, error) VALUES (?, datetime('now','localtime'), ?)",
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
               SET category=?, equipment=?, cue=?
               WHERE name=?""",
            (
                ex["category"],
                ex["equipment"],
                ex["cue"],
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
    await conn.execute("COMMIT")
    logging.info("Seeded %d exercises, %d global routines", len(EXERCISES), len(ROUTINES))


async def open_db(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
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
    global _conn
    _conn = None
