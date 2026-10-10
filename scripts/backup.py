#!/usr/bin/env python3
"""Hot backup of the Zenkai SQLite database.

sqlite3.Connection.backup() is WAL-safe and works while the app is live —
it checkpoints WAL and copies atomically so the backup is always consistent.

Usage:
    DATABASE_PATH=./fittrack.db python3 scripts/backup.py

Environment variables:
    DATABASE_PATH  path to fittrack.db (required)
    BACKUP_DIR     directory for backup files (default: ./backups next to the db)
    KEEP_DAYS      number of daily backups to retain (default: 7)
    DB_ENCRYPTION_KEY  the app's SQLCipher key, when the database is
                       encrypted; the backup is encrypted with the same key
    MIRROR_DIR     optional second copy on another device (e.g. a USB drive:
                   /media/<user>/<drive>/zenkai-backups). Its parent must
                   already exist, so an unplugged drive fails loudly instead of
                   filling a folder on the SD card. The local backup is kept
                   either way.
    MIRROR_KEEP    number of backups to retain in MIRROR_DIR (default: 30)
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _connect(path: Path, key: str):
    """Open `path` the way the app does: SQLCipher with the key, or plain."""
    if not key:
        return sqlite3.connect(path)
    import sqlcipher3.dbapi2 as sqlcipher3

    conn = sqlcipher3.connect(path)
    conn.execute(f"PRAGMA key=\"x'{key}'\"")
    return conn


def verify(path: Path, key: str) -> dict[str, int]:
    """Open a backup the way a restore would and check every page.

    Returns row counts for the tables a restore cares about; raises when the
    key is wrong or PRAGMA integrity_check finds damage.
    """
    conn = _connect(path, key)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check: {result}")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                for t in ("users", "workouts", "sets") if t in tables}
    finally:
        conn.close()


def _prune(directory: Path, keep: int) -> None:
    for old in sorted(directory.glob("fittrack-*.db"))[:-keep]:
        old.unlink()
        print(f"Removed old backup: {old}")


def _mirror(dest: Path, mirror_dir: Path, keep: int, key: str) -> None:
    if not mirror_dir.parent.is_dir():
        raise RuntimeError(f"{mirror_dir.parent} does not exist (drive not mounted?)")
    mirror_dir.mkdir(exist_ok=True)
    final = mirror_dir / dest.name
    partial = mirror_dir / (dest.name + ".partial")
    shutil.copyfile(dest, partial)
    try:
        verify(partial, key)   # catches a bad write to the drive
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(final)
    print(f"Mirrored → {final}")
    _prune(mirror_dir, keep)


def main() -> None:
    db_path = os.environ.get("DATABASE_PATH", "").strip()
    if not db_path:
        sys.exit("DATABASE_PATH is not set")

    src = Path(db_path).resolve()
    if not src.exists():
        sys.exit(f"Database not found: {src}")

    backup_dir = Path(os.environ.get("BACKUP_DIR", src.parent / "backups")).resolve()
    keep_days = int(os.environ.get("KEEP_DAYS", "7"))

    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"fittrack-{stamp}.db"

    key = os.environ.get("DB_ENCRYPTION_KEY", "").strip()
    try:
        src_conn = _connect(src, key)
        dst_conn = _connect(dest, key)
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
        # Read the copy back, so a wrong key or a broken copy fails here
        # instead of leaving a file that only looks like a backup.
        counts = verify(dest, key)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        hint = "" if key else " (is it encrypted? set DB_ENCRYPTION_KEY)"
        sys.exit(f"Backup failed: {exc}{hint}")

    size_kb = dest.stat().st_size // 1024
    summary = ", ".join(f"{n} {t}" for t, n in counts.items())
    print(f"Backed up {src} → {dest} ({size_kb} KB; integrity ok{'; ' + summary if summary else ''})")

    # Prune oldest backups, keeping the most recent keep_days files.
    _prune(backup_dir, keep_days)

    mirror = os.environ.get("MIRROR_DIR", "").strip()
    if mirror:
        try:
            _mirror(dest, Path(mirror), int(os.environ.get("MIRROR_KEEP", "30")), key)
        except Exception as exc:
            sys.exit(f"Mirror failed (local backup kept): {exc}")


if __name__ == "__main__":
    main()
