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
]


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA.read_text())
    for sql in _MIGRATIONS:
        try:
            await conn.execute(sql)
        except Exception:
            pass

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
