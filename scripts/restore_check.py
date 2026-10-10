#!/usr/bin/env python3
"""Restore drill: prove the newest backup in each folder can be restored.

For every folder given, opens its newest fittrack-*.db with the app's key, runs
PRAGMA integrity_check, prints row counts, and fails if the backup is damaged,
unreadable, or older than MAX_AGE_DAYS. A file path checks just that file.

Usage:
    DB_ENCRYPTION_KEY=... python3 scripts/restore_check.py backups/ /media/<user>/<drive>/zenkai-backups

Environment variables:
    DB_ENCRYPTION_KEY  the SQLCipher key the backups were made with (omit for plain)
    MAX_AGE_DAYS       newest backup must be younger than this (default: 3)

To restore: stop the service, copy the chosen backup over fittrack.db (delete
fittrack.db-wal and fittrack.db-shm first), start the service.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import verify  # noqa: E402


def check(target: Path, key: str, max_age_days: float) -> bool:
    if target.is_dir():
        backups = sorted(target.glob("fittrack-*.db"))
        if not backups:
            print(f"FAIL  {target}: no backups found")
            return False
        newest = backups[-1]
    else:
        newest = target
    try:
        counts = verify(newest, key)
    except Exception as exc:
        print(f"FAIL  {newest}: {exc}")
        return False
    age_days = (time.time() - newest.stat().st_mtime) / 86400
    summary = ", ".join(f"{n} {t}" for t, n in counts.items()) or "no app tables"
    if age_days > max_age_days:
        print(f"FAIL  {newest}: {age_days:.1f} days old (limit {max_age_days:g}); {summary}")
        return False
    print(f"OK    {newest}: integrity ok, {age_days:.1f} days old; {summary}")
    return True


def main() -> None:
    targets = [Path(a) for a in sys.argv[1:]]
    if not targets:
        sys.exit(__doc__)
    key = os.environ.get("DB_ENCRYPTION_KEY", "").strip()
    max_age = float(os.environ.get("MAX_AGE_DAYS", "3"))
    results = [check(t, key, max_age) for t in targets]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
