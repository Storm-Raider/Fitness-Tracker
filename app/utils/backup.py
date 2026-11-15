"""Backups of the database: the copy itself, the nightly schedule and its status.

The app backs itself up (app/main.py runs backup_loop), so a fresh install is
protected without a cron job. scripts/backup.py runs the same code by hand.

Each backup is a hot copy (sqlite's backup API, safe while the app is live),
read back with PRAGMA integrity_check before it counts. Settings, all optional:

    BACKUP_DIR          where copies go (default: backups/ next to the database)
    KEEP_DAYS           how many to keep there (default 7)
    BACKUP_EVERY_HOURS  a new one when the newest is older than this (default 24;
                        0 turns the schedule off)
    MIRROR_DIR          a second copy on another device, e.g. a USB drive. Its
                        parent must exist, so an unplugged drive fails loudly
                        instead of filling a folder on the SD card.
    MIRROR_KEEP         how many to keep there (default 30)
    DB_ENCRYPTION_KEY   the app's SQLCipher key; backups are encrypted with it
"""
import asyncio
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

PATTERN = "fittrack-*.db"
STALE_AFTER_DAYS = 3          # Settings warns past this (as scripts/restore_check.py does)
_CHECK_EVERY_S = 3600
_FIRST_CHECK_AFTER_S = 300    # not during startup, and not in short-lived test servers


class BackupError(Exception):
    pass


class MirrorError(BackupError):
    """The local backup was made (`.result`); the second copy failed."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


def db_path() -> str:
    """The live database (same default as app/main.py)."""
    return os.environ.get("DATABASE_PATH", "/data/fitness.db")


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
    for old in sorted(directory.glob(PATTERN))[:-keep]:
        old.unlink()
        log.info("removed old backup %s", old)


def _mirror(dest: Path, mirror_dir: Path, keep: int, key: str) -> Path:
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
    _prune(mirror_dir, keep)
    return final


def settings(db_path: str) -> dict:
    """Where backups go and how many to keep, from the environment."""
    src = Path(db_path).resolve()
    env = os.environ.get
    return {
        "src": src,
        "backup_dir": Path(env("BACKUP_DIR", "").strip() or src.parent / "backups").resolve(),
        "keep": int(env("KEEP_DAYS", "7")),
        "key": env("DB_ENCRYPTION_KEY", "").strip(),
        "mirror_dir": Path(m) if (m := env("MIRROR_DIR", "").strip()) else None,
        "mirror_keep": int(env("MIRROR_KEEP", "30")),
    }


def run(db_path: str) -> dict:
    """Make one backup now. Returns {path, size_kb, counts, mirrored}.

    Raises BackupError when no backup was made, MirrorError when the local one
    was made but the second copy failed."""
    cfg = settings(db_path)
    src, key = cfg["src"], cfg["key"]
    if not src.exists():
        raise BackupError(f"Database not found: {src}")
    cfg["backup_dir"].mkdir(parents=True, exist_ok=True)
    dest = cfg["backup_dir"] / f"fittrack-{datetime.now():%Y%m%d-%H%M%S}.db"
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
        raise BackupError(f"{exc}{hint}") from exc
    result = {"path": dest, "size_kb": dest.stat().st_size // 1024, "counts": counts, "mirrored": None}
    _prune(cfg["backup_dir"], cfg["keep"])
    if cfg["mirror_dir"]:
        try:
            result["mirrored"] = _mirror(dest, cfg["mirror_dir"], cfg["mirror_keep"], key)
        except Exception as exc:
            raise MirrorError(str(exc), result) from exc
    return result


# ── Schedule and status (the running app) ────────────────────────────────

_lock = asyncio.Lock()
_last_error: str | None = None        # the latest run's failure, cleared by a success


def _newest(directory: Path) -> Path | None:
    files = sorted(directory.glob(PATTERN)) if directory.is_dir() else []
    return files[-1] if files else None


def _folder(directory: Path | None) -> dict | None:
    if directory is None:
        return None
    newest = _newest(directory)
    if newest is None:
        return {"dir": str(directory), "count": 0, "newest": None}
    st = newest.stat()
    return {
        "dir": str(directory), "count": len(list(directory.glob(PATTERN))),
        "newest": newest.name, "path": newest,
        "size_text": f"{kb} KB" if (kb := st.st_size // 1024) < 1024 else f"{kb / 1024:.1f} MB",
        "age_hours": max(0.0, (time.time() - st.st_mtime) / 3600),
        "when_text": _when_text(datetime.fromtimestamp(st.st_mtime)),
    }


def _when_text(when: datetime) -> str:
    """'Today, 3:30 AM' / 'Yesterday, 3:30 AM' / 'Nov 12, 3:30 AM'."""
    days = (datetime.now().date() - when.date()).days
    day = "Today" if days == 0 else "Yesterday" if days == 1 else f"{when:%b} {when.day}"
    return f"{day}, {when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"


def every_hours() -> float:
    try:
        return max(0.0, float(os.environ.get("BACKUP_EVERY_HOURS", "24")))
    except ValueError:
        return 24.0


def status(db_path: str) -> dict:
    """What Settings shows: the newest local backup, the second copy, any failure."""
    cfg = settings(db_path)
    local = _folder(cfg["backup_dir"])
    return {
        "local": local,
        "mirror": _folder(cfg["mirror_dir"]),
        "scheduled": every_hours() > 0,
        "stale": local["newest"] is None or local["age_hours"] > STALE_AFTER_DAYS * 24,
        "error": _last_error,
    }


async def run_now(db_path: str) -> dict:
    """One backup, off the event loop; remembers a failure for status()."""
    global _last_error
    async with _lock:
        try:
            result = await asyncio.to_thread(run, db_path)
        except MirrorError as exc:
            _last_error = f"The second copy failed: {exc}"
            raise
        except BackupError as exc:
            _last_error = f"The last backup failed: {exc}"
            raise
        _last_error = None
        return result


async def backup_loop(db_path: str) -> None:
    """A backup whenever the newest is older than BACKUP_EVERY_HOURS. Checked hourly
    by the file's age, not a clock time, so restarts and deploys don't skip a night."""
    await asyncio.sleep(_FIRST_CHECK_AFTER_S)
    while True:
        hours = every_hours()
        try:
            newest = _folder(settings(db_path)["backup_dir"])
            if hours > 0 and (newest["newest"] is None or newest["age_hours"] >= hours):
                result = await run_now(db_path)
                log.info("backup: %s (%d KB)", result["path"], result["size_kb"])
        except Exception:                           # never let the loop die
            log.exception("backup: scheduled run failed")
        await asyncio.sleep(_CHECK_EVERY_S)
