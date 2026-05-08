-- Fitness Tracker v1 schema
-- Datetimes stored as local naive ISO strings (datetime.now().isoformat()).
-- Requires Pi timezone to be set correctly: sudo timedatectl set-timezone <tz>
-- exercises: global shared library — intentionally NO user_id.
-- workouts/sets/body_metrics: user_id scaffold (DEFAULT 1) for v2 multi-user migration.

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS exercises (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    started_at  DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    ended_at    DATETIME NULL,
    notes       TEXT,
    user_id     INTEGER  NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id  INTEGER NOT NULL REFERENCES workouts(id),
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    reps        INTEGER NOT NULL,
    weight_kg   REAL    NOT NULL,
    notes       TEXT,
    user_id     INTEGER NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS body_metrics (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    recorded_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    weight_kg   REAL     NOT NULL,
    calories    INTEGER  NULL,
    user_id     INTEGER  NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sets_exercise ON sets(exercise_id);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    username      TEXT     NOT NULL UNIQUE,
    password_hash TEXT     NOT NULL,
    is_admin      INTEGER  NOT NULL DEFAULT 0,
    created_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS invite_tokens (
    token      TEXT     PRIMARY KEY,
    created_by INTEGER  NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at DATETIME NOT NULL,
    used_at    DATETIME NULL,
    used_by    INTEGER  NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS routines (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT    NOT NULL,
    user_id INTEGER NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS routine_exercises (
    routine_id  INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    order_idx   INTEGER NOT NULL DEFAULT 0
);
