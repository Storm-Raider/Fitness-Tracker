#!/usr/bin/env python3
"""Hot backup of the FitStorm SQLite database.

sqlite3.Connection.backup() is WAL-safe and works while the app is live —
it checkpoints WAL and copies atomically so the backup is always consistent.

Usage:
    DATABASE_PATH=./fittrack.db python3 scripts/backup.py

Environment variables:
    DATABASE_PATH  path to fittrack.db (required)
    BACKUP_DIR     directory for backup files (default: ./backups next to the db)
    KEEP_DAYS      number of daily backups to retain (default: 7)
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


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

    with sqlite3.connect(src) as src_conn, sqlite3.connect(dest) as dst_conn:
        src_conn.backup(dst_conn)

    size_kb = dest.stat().st_size // 1024
    print(f"Backed up {src} → {dest} ({size_kb} KB)")

    # Prune oldest backups, keeping the most recent keep_days files.
    all_backups = sorted(backup_dir.glob("fittrack-*.db"))
    for old in all_backups[:-keep_days]:
        old.unlink()
        print(f"Removed old backup: {old.name}")


if __name__ == "__main__":
    main()
