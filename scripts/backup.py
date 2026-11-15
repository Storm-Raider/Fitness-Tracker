#!/usr/bin/env python3
"""Hot backup of the Zenkai SQLite database.

sqlite3.Connection.backup() is WAL-safe and works while the app is live —
it checkpoints WAL and copies atomically so the backup is always consistent.

The running app makes one of these every night by itself (app/utils/backup.py,
which this script calls). Use this for a backup by hand or from cron.

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

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.utils.backup import BackupError, MirrorError, run, verify  # noqa: E402,F401  (verify: restore_check.py)


def main() -> None:
    db_path = os.environ.get("DATABASE_PATH", "").strip()
    if not db_path:
        sys.exit("DATABASE_PATH is not set")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    mirror_error = None
    try:
        result = run(db_path)
    except MirrorError as exc:
        result, mirror_error = exc.result, exc
    except BackupError as exc:
        sys.exit(f"Backup failed: {exc}")
    summary = ", ".join(f"{n} {t}" for t, n in result["counts"].items())
    print(f"Backed up {Path(db_path).resolve()} → {result['path']} "
          f"({result['size_kb']} KB; integrity ok{'; ' + summary if summary else ''})")
    if mirror_error:
        sys.exit(f"Mirror failed (local backup kept): {mirror_error}")
    if result["mirrored"]:
        print(f"Mirrored → {result['mirrored']}")


if __name__ == "__main__":
    main()
