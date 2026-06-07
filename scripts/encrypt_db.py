#!/usr/bin/env python3
"""
One-time migration: encrypt an existing plaintext fitness.db with SQLCipher.

Prerequisites:
  pip install sqlcipher3

Usage:
  Generate a key:
    python3 -c "import secrets; print(secrets.token_hex(32))"

  Add it to .env:
    DB_ENCRYPTION_KEY=<the generated key>

  Run this script (stops service first, encrypts, then restart manually):
    DB_ENCRYPTION_KEY=<key> python scripts/encrypt_db.py [/path/to/fitness.db]

The original database is backed up to fitness.db.plain.bak.
Remove the backup only after confirming the app starts and data is intact.
"""

import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    key = os.environ.get("DB_ENCRYPTION_KEY", "").strip()
    if not key:
        log.error("DB_ENCRYPTION_KEY environment variable is required.")
        log.error('Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))"')
        sys.exit(1)

    try:
        bytes.fromhex(key)
    except ValueError:
        log.error("DB_ENCRYPTION_KEY must be a hex string (64 hex characters from secrets.token_hex(32)).")
        sys.exit(1)

    try:
        import sqlcipher3.dbapi2 as sqlcipher
    except ImportError:
        log.error("sqlcipher3 not installed. Run: pip install sqlcipher3")
        sys.exit(1)

    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/fitness.db")
    if not db_path.exists():
        log.error("Database not found: %s", db_path)
        sys.exit(1)

    # Guard: make sure the DB is actually plaintext before migrating
    try:
        plain_check = sqlite3.connect(str(db_path))
        plain_check.execute("SELECT count(*) FROM sqlite_master").fetchone()
        plain_check.close()
    except sqlite3.DatabaseError:
        log.error(
            "%s could not be opened as plaintext — it may already be encrypted, "
            "or the file is corrupt. If you need to re-encrypt, restore the .bak file first.",
            db_path,
        )
        sys.exit(1)

    # Dump the plaintext DB to SQL
    log.info("Reading plaintext database: %s", db_path)
    plain = sqlite3.connect(str(db_path))
    dump = list(plain.iterdump())
    plain.close()
    log.info("Read %d SQL statements.", len(dump))

    # Write to a temporary encrypted file alongside the original
    tmp_path = db_path.with_suffix(".db.enc_tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    log.info("Writing encrypted database to: %s", tmp_path)
    enc = sqlcipher.connect(str(tmp_path))
    enc.execute(f"PRAGMA key=\"x'{key}'\"")
    enc.execute("PRAGMA journal_mode=WAL")
    enc.execute("BEGIN")
    skipped = 0
    for sql in dump:
        if sql in ("BEGIN TRANSACTION;", "COMMIT;"):
            continue
        try:
            enc.execute(sql)
        except Exception as exc:
            if "PRAGMA" in sql:
                # SQLite dumps include PRAGMA statements that don't replay cleanly
                skipped += 1
            else:
                log.warning("Skipping (error: %s): %.80s", exc, sql)
                skipped += 1
    enc.execute("COMMIT")
    enc.close()
    if skipped:
        log.info("Skipped %d statements (PRAGMA or harmless errors).", skipped)

    # Validate the encrypted DB opens correctly
    log.info("Validating encrypted database...")
    try:
        check = sqlcipher.connect(str(tmp_path))
        check.execute(f"PRAGMA key=\"x'{key}'\"")
        count = check.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        check.close()
        log.info("Validation passed: %d tables/indexes.", count)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        log.error("Validation failed — encrypted DB is unreadable: %s", exc)
        sys.exit(1)

    # Backup original, then swap in encrypted copy
    bak_path = db_path.with_suffix(".db.plain.bak")
    shutil.copy2(db_path, bak_path)
    log.info("Backup saved: %s", bak_path)
    shutil.move(str(tmp_path), str(db_path))

    log.info("")
    log.info("Done. %s is now SQLCipher-encrypted.", db_path)
    log.info("Next steps:")
    log.info("  1. Ensure DB_ENCRYPTION_KEY is set in your .env")
    log.info("  2. Restart the service: sudo systemctl restart fitstorm")
    log.info("  3. Verify the app loads and your data is intact")
    log.info("  4. Remove the backup: rm %s", bak_path)


if __name__ == "__main__":
    main()
