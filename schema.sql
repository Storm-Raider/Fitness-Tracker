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
