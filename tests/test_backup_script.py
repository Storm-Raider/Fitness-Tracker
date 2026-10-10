"""scripts/backup.py must produce a readable copy of an encrypted database."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup.py"
KEY = "ab" * 32

sqlcipher3 = pytest.importorskip("sqlcipher3.dbapi2")


def _keyed(path, key=KEY):
    conn = sqlcipher3.connect(path)
    conn.execute(f"PRAGMA key=\"x'{key}'\"")
    return conn


def _run(db, backups, key=None):
    env = {k: v for k, v in os.environ.items() if k != "DB_ENCRYPTION_KEY"}
    env.update(DATABASE_PATH=str(db), BACKUP_DIR=str(backups))
    if key is not None:
        env["DB_ENCRYPTION_KEY"] = key
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)


@pytest.fixture
def encrypted_db(tmp_path):
    db = tmp_path / "fittrack.db"
    conn = _keyed(db)
    conn.execute("CREATE TABLE workouts(id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO workouts(name) VALUES ('Leg day')")
    conn.commit()
    conn.close()
    return db


def test_an_encrypted_database_is_copied_and_stays_encrypted(encrypted_db, tmp_path):
    backups = tmp_path / "backups"
    result = _run(encrypted_db, backups, KEY)
    assert result.returncode == 0, result.stderr
    [copy] = list(backups.glob("fittrack-*.db"))
    assert _keyed(copy).execute("SELECT name FROM workouts").fetchall() == [("Leg day",)]
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(copy).execute("SELECT count(*) FROM sqlite_master").fetchone()


def test_a_missing_key_fails_loudly_and_leaves_no_empty_file(encrypted_db, tmp_path):
    backups = tmp_path / "backups"
    result = _run(encrypted_db, backups)
    assert result.returncode != 0
    assert "DB_ENCRYPTION_KEY" in result.stderr
    assert list(backups.glob("fittrack-*.db")) == []


def test_a_wrong_key_fails_and_leaves_no_file(encrypted_db, tmp_path):
    backups = tmp_path / "backups"
    result = _run(encrypted_db, backups, "cd" * 32)
    assert result.returncode != 0
    assert list(backups.glob("fittrack-*.db")) == []


def test_a_plain_database_still_backs_up(tmp_path):
    db = tmp_path / "fittrack.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE workouts(id INTEGER PRIMARY KEY)")
    backups = tmp_path / "backups"
    result = _run(db, backups)
    assert result.returncode == 0, result.stderr
    [copy] = list(backups.glob("fittrack-*.db"))
    assert sqlite3.connect(copy).execute("SELECT name FROM sqlite_master").fetchall() == [("workouts",)]
