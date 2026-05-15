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

CREATE INDEX IF NOT EXISTS idx_sets_exercise          ON sets(exercise_id);
CREATE INDEX IF NOT EXISTS idx_workouts_user_started  ON workouts(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sets_user_exercise     ON sets(user_id, exercise_id);
CREATE INDEX IF NOT EXISTS idx_sets_workout_user      ON sets(workout_id, user_id);
CREATE INDEX IF NOT EXISTS idx_metrics_user_recorded  ON body_metrics(user_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    username      TEXT     NOT NULL UNIQUE,
    password_hash TEXT     NOT NULL,
    is_admin      INTEGER  NOT NULL DEFAULT 0,
    email         TEXT,
    created_at    DATETIME NOT NULL DEFAULT (datetime('now','localtime'))
);

-- idx_users_email is created by the _MIGRATIONS runner in db.py after the
-- email column is guaranteed to exist.  Putting it here causes executescript()
-- to fail with "no such column: email" on databases that pre-date the column.

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT     PRIMARY KEY,
    user_id    INTEGER  NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at DATETIME NOT NULL,
    used_at    DATETIME NULL
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

CREATE TABLE IF NOT EXISTS exercise_muscles (
    exercise_id  INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    muscle       TEXT    NOT NULL,
    is_primary   BOOLEAN NOT NULL DEFAULT 1,
    PRIMARY KEY (exercise_id, muscle)
);

-- Built-in exercise library (INSERT OR IGNORE so existing user data is never clobbered)
INSERT OR IGNORE INTO exercises(name) VALUES
  ('Ab Wheel Rollout'),
  ('Arnold Press'),
  ('Back Squat'),
  ('Barbell Curl'),
  ('Barbell Row'),
  ('Barbell Shrug'),
  ('Bench Press'),
  ('Box Jump'),
  ('Bulgarian Split Squat'),
  ('Burpee'),
  ('Cable Curl'),
  ('Cable Fly'),
  ('Cable Lateral Raise'),
  ('Cable Tricep Kickback'),
  ('Calf Raise'),
  ('Chest Dip'),
  ('Chin-up'),
  ('Clean and Jerk'),
  ('Close-Grip Bench Press'),
  ('Concentration Curl'),
  ('Crunch'),
  ('Cycling'),
  ('Deadlift'),
  ('Decline Bench Press'),
  ('Diamond Push-up'),
  ('Dip'),
  ('Dragon Flag'),
  ('Dumbbell Bench Press'),
  ('Dumbbell Curl'),
  ('Dumbbell Fly'),
  ('Dumbbell Row'),
  ('Dumbbell Shoulder Press'),
  ('Elliptical'),
  ('EZ-Bar Curl'),
  ('Face Pull'),
  ('Front Raise'),
  ('Front Squat'),
  ('Glute Bridge'),
  ('Goblet Squat'),
  ('Hack Squat'),
  ('Hammer Curl'),
  ('Hanging Leg Raise'),
  ('Hip Thrust'),
  ('Incline Bench Press'),
  ('Incline Dumbbell Curl'),
  ('Incline Dumbbell Press'),
  ('Jump Rope'),
  ('Kettlebell Swing'),
  ('Lat Pulldown'),
  ('Lateral Raise'),
  ('Leg Curl'),
  ('Leg Extension'),
  ('Leg Press'),
  ('Lunge'),
  ('Machine Shoulder Press'),
  ('Meadows Row'),
  ('Overhead Press'),
  ('Overhead Tricep Extension'),
  ('Pec Deck'),
  ('Plank'),
  ('Power Clean'),
  ('Preacher Curl'),
  ('Pull-up'),
  ('Push-up'),
  ('Rack Pull'),
  ('Rear Delt Fly'),
  ('Reverse Crunch'),
  ('Romanian Deadlift'),
  ('Rowing Machine'),
  ('Running'),
  ('Russian Twist'),
  ('Seated Cable Row'),
  ('Seated Calf Raise'),
  ('Single-Arm Dumbbell Row'),
  ('Sit-up'),
  ('Skull Crusher'),
  ('Step-up'),
  ('Straight-Arm Pulldown'),
  ('Sumo Deadlift'),
  ('T-Bar Row'),
  ('Thruster'),
  ('Tricep Dip'),
  ('Tricep Pushdown'),
  ('Turkish Get-up'),
  ('Upright Row'),
  ('Walking Lunge');
