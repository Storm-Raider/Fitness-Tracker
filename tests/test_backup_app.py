"""The app backs itself up (app/utils/backup.py): schedule, status, admin routes."""
import asyncio
import os
import sqlite3
import time

import pytest

from app.utils import backup


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    db = tmp_path / "fittrack.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE workouts(id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO workouts(name) VALUES ('Leg day')")
    conn.commit()
    conn.close()
    for var in ("BACKUP_DIR", "MIRROR_DIR", "KEEP_DAYS", "MIRROR_KEEP", "DB_ENCRYPTION_KEY", "BACKUP_EVERY_HOURS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setattr(backup, "_last_error", None)
    return db


def _age(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_status_before_and_after_a_backup(live_db):
    st = backup.status(str(live_db))
    assert st["local"]["newest"] is None and st["stale"] and st["scheduled"] and st["mirror"] is None

    result = backup.run(str(live_db))
    assert result["counts"] == {"workouts": 1}
    st = backup.status(str(live_db))
    assert st["local"]["count"] == 1 and not st["stale"]
    assert st["local"]["when_text"].startswith("Today, ")

    _age(result["path"], 4 * 24)
    assert backup.status(str(live_db))["stale"]


@pytest.mark.asyncio
async def test_loop_backs_up_only_when_the_newest_is_old(live_db, monkeypatch):
    monkeypatch.setattr(backup, "_FIRST_CHECK_AFTER_S", 0)
    monkeypatch.setattr(backup, "_CHECK_EVERY_S", 0.05)
    folder = live_db.parent / "backups"

    async def spin():
        task = asyncio.create_task(backup.backup_loop(str(live_db)))
        await asyncio.sleep(0.4)
        task.cancel()

    await spin()                                      # none yet: makes one, then leaves it
    files = list(folder.glob("fittrack-*.db"))
    assert len(files) == 1

    _age(files[0], 23)                                # 23 h old: not due
    await spin()
    assert len(list(folder.glob("fittrack-*.db"))) == 1

    monkeypatch.setenv("BACKUP_EVERY_HOURS", "0")     # due, but switched off
    _age(files[0], 30)
    await spin()
    assert len(list(folder.glob("fittrack-*.db"))) == 1


@pytest.mark.asyncio
async def test_a_failed_mirror_keeps_the_local_backup_and_is_reported(live_db, monkeypatch):
    monkeypatch.setenv("MIRROR_DIR", str(live_db.parent / "unplugged" / "zenkai-backups"))
    with pytest.raises(backup.MirrorError):
        await backup.run_now(str(live_db))
    st = backup.status(str(live_db))
    assert st["local"]["count"] == 1
    assert "second copy failed" in st["error"]

    monkeypatch.delenv("MIRROR_DIR")
    await backup.run_now(str(live_db))                # a success clears it
    assert backup.status(str(live_db))["error"] is None


@pytest.mark.asyncio
async def test_admin_backs_up_and_downloads_from_settings(admin_client, live_db):
    page = await admin_client.get("/settings", headers={"Accept": "text/html"})
    assert "No backup yet." in page.text and 'id="backup-now"' in page.text

    assert (await admin_client.get("/settings/backup/latest")).status_code == 404
    resp = await admin_client.post("/settings/backup")
    assert resp.status_code == 200 and resp.json() == {"ok": True}

    page = await admin_client.get("/settings", headers={"Accept": "text/html"})
    assert "Last backup:" in page.text and "Today, " in page.text and "Download latest" in page.text

    got = await admin_client.get("/settings/backup/latest")
    assert got.status_code == 200 and got.content[:15] == b"SQLite format 3"
    assert "fittrack-" in got.headers["content-disposition"]


@pytest.mark.asyncio
async def test_members_get_no_backup_card_or_routes(client, live_db):
    page = await client.get("/settings", headers={"Accept": "text/html"})
    assert "backup-card" not in page.text
    assert (await client.post("/settings/backup")).status_code == 403
    assert (await client.get("/settings/backup/latest")).status_code == 403
    assert not (live_db.parent / "backups").exists()
