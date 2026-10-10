"""scripts/restore_check.py: prove the newest backup in each folder restores."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "restore_check.py"
KEY = "ab" * 32

sqlcipher3 = pytest.importorskip("sqlcipher3.dbapi2")


def _make_backup(path: Path, key=KEY):
    conn = sqlcipher3.connect(path)
    conn.execute(f"PRAGMA key=\"x'{key}'\"")
    conn.execute("CREATE TABLE workouts(id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO workouts DEFAULT VALUES")
    conn.commit()
    conn.close()


def _run(*targets, key=KEY, **extra):
    env = {k: v for k, v in os.environ.items() if k != "DB_ENCRYPTION_KEY"}
    env.update(DB_ENCRYPTION_KEY=key, **extra)
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, targets)],
                          env=env, capture_output=True, text=True)


def test_newest_backup_in_a_folder_passes(tmp_path):
    _make_backup(tmp_path / "fittrack-20260101-000000.db")
    _make_backup(tmp_path / "fittrack-20260102-000000.db")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "fittrack-20260102-000000.db" in result.stdout
    assert "1 workouts" in result.stdout


def test_a_damaged_newest_backup_fails(tmp_path):
    _make_backup(tmp_path / "fittrack-20260101-000000.db")
    (tmp_path / "fittrack-20260102-000000.db").write_bytes(b"not a database" * 100)
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "fittrack-20260102-000000.db" in result.stdout + result.stderr


def test_the_wrong_key_fails(tmp_path):
    _make_backup(tmp_path / "fittrack-20260101-000000.db")
    assert _run(tmp_path, key="cd" * 32).returncode != 0


def test_an_empty_folder_fails(tmp_path):
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "no backups" in (result.stdout + result.stderr).lower()


def test_a_stale_newest_backup_fails(tmp_path):
    backup = tmp_path / "fittrack-20260101-000000.db"
    _make_backup(backup)
    old = time.time() - 5 * 86400
    os.utime(backup, (old, old))
    result = _run(tmp_path, MAX_AGE_DAYS="3")
    assert result.returncode != 0
    assert "days old" in result.stdout + result.stderr


def test_every_folder_is_checked(tmp_path):
    local, usb = tmp_path / "local", tmp_path / "usb"
    local.mkdir(); usb.mkdir()
    _make_backup(local / "fittrack-20260101-000000.db")
    result = _run(local, usb)
    assert result.returncode != 0          # usb is empty
    assert str(local) in result.stdout     # local still reported
