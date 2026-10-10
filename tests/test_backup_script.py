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


def _run(db, backups, key=None, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("DB_ENCRYPTION_KEY", "MIRROR_DIR", "MIRROR_KEEP")}
    env.update(DATABASE_PATH=str(db), BACKUP_DIR=str(backups), **extra)
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


# ── Off-device mirror (MIRROR_DIR) ───────────────────────────────────────────

def test_the_backup_is_mirrored_to_the_drive_and_readable(encrypted_db, tmp_path):
    drive = tmp_path / "usb"
    drive.mkdir()
    mirror = drive / "zenkai-backups"
    result = _run(encrypted_db, tmp_path / "backups", KEY, MIRROR_DIR=str(mirror))
    assert result.returncode == 0, result.stderr
    [copy] = list(mirror.glob("fittrack-*.db"))
    assert _keyed(copy).execute("SELECT name FROM workouts").fetchall() == [("Leg day",)]


def test_an_unmounted_drive_fails_loudly_but_keeps_the_local_backup(encrypted_db, tmp_path):
    """With the drive unplugged its mount point is gone; the script must not
    quietly create the folder on the SD card instead."""
    backups = tmp_path / "backups"
    mirror = tmp_path / "unplugged" / "zenkai-backups"
    result = _run(encrypted_db, backups, KEY, MIRROR_DIR=str(mirror))
    assert result.returncode != 0
    assert "not mounted" in result.stderr
    assert len(list(backups.glob("fittrack-*.db"))) == 1
    assert not (tmp_path / "unplugged").exists()


def test_the_mirror_keeps_its_own_count(encrypted_db, tmp_path):
    drive = tmp_path / "usb"
    mirror = drive / "zenkai-backups"
    mirror.mkdir(parents=True)
    for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
        (mirror / f"fittrack-{stamp}.db").write_bytes(b"old")
    result = _run(encrypted_db, tmp_path / "backups", KEY, MIRROR_DIR=str(mirror), MIRROR_KEEP="2")
    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in mirror.glob("fittrack-*.db"))
    assert len(names) == 2
    assert names[0] == "fittrack-20200103-000000.db"


def test_the_copy_passes_an_integrity_check(encrypted_db, tmp_path):
    """The copy is checked with PRAGMA integrity_check, not just opened."""
    result = _run(encrypted_db, tmp_path / "backups", KEY)
    assert result.returncode == 0, result.stderr
    assert "integrity ok" in result.stdout.lower()
