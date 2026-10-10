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
"""

import os
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
            # Read the copy back, so a wrong key or a broken copy fails here
            # instead of leaving an empty file that looks like a backup.
            dst_conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            src_conn.close()
            dst_conn.close()
    except Exception as exc:
        dest.unlink(missing_ok=True)
        hint = "" if key else " (is it encrypted? set DB_ENCRYPTION_KEY)"
        sys.exit(f"Backup failed: {exc}{hint}")

    size_kb = dest.stat().st_size // 1024
    print(f"Backed up {src} → {dest} ({size_kb} KB)")

    # Prune oldest backups, keeping the most recent keep_days files.
    all_backups = sorted(backup_dir.glob("fittrack-*.db"))
    for old in all_backups[:-keep_days]:
        old.unlink()
        print(f"Removed old backup: {old.name}")


if __name__ == "__main__":
    main()
