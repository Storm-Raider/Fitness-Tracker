"""Writes from one request never land inside another request's transaction.

Every request shares one autocommit connection. Before this fix, a plain
write made while another request held a write_tx ran inside that transaction:
if the transaction rolled back, the unrelated write vanished, though its
request had already answered 200. Writes now wait for the open transaction;
reads do not.
"""
import asyncio
import re
from pathlib import Path

import pytest

import app.db
from app.db import write_tx

APP = Path(__file__).resolve().parent.parent / "app"


async def _names(db, like):
    async with db.execute("SELECT name FROM exercises WHERE name LIKE ?", (like,)) as cur:
        return {r["name"] for r in await cur.fetchall()}


async def _hold_tx_then_fail(db, started, release, write_sql):
    """A write_tx that writes, waits, then raises (so it rolls back)."""
    with pytest.raises(RuntimeError):
        async with write_tx(db):
            await db.execute(write_sql)
            started.set()
            await release.wait()
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_a_write_outside_the_transaction_survives_its_rollback(db):
    started, release = asyncio.Event(), asyncio.Event()
    tx = asyncio.create_task(_hold_tx_then_fail(
        db, started, release, "INSERT INTO exercises(name) VALUES ('iso-tx-row')"))
    await started.wait()

    async def outside():
        await db.execute("INSERT INTO exercises(name) VALUES ('iso-outside-row')")
    other = asyncio.create_task(outside())
    await asyncio.sleep(0.05)
    release.set()
    await tx
    await other

    assert await _names(db, "iso-%") == {"iso-outside-row"}


@pytest.mark.asyncio
async def test_a_journal_save_during_a_failed_transaction_is_kept(client, db):
    """The same race through a real route: the save answered 200, so it must persist."""
    started, release = asyncio.Event(), asyncio.Event()
    tx = asyncio.create_task(_hold_tx_then_fail(
        db, started, release, "INSERT INTO exercises(name) VALUES ('iso-import-row')"))
    await started.wait()

    save = asyncio.create_task(client.post("/journal", json={"log_date": "2026-01-02", "steps": 1234}))
    await asyncio.sleep(0.05)
    release.set()
    await tx
    resp = await save
    assert resp.status_code in (200, 201), resp.text

    async with db.execute("SELECT steps FROM daily_logs WHERE user_id = 1 AND log_date = '2026-01-02'") as cur:
        row = await cur.fetchone()
    assert row is not None and row["steps"] == 1234


@pytest.mark.asyncio
async def test_reads_do_not_wait_for_an_open_transaction(db):
    started, release = asyncio.Event(), asyncio.Event()
    tx = asyncio.create_task(_hold_tx_then_fail(
        db, started, release, "INSERT INTO exercises(name) VALUES ('iso-read-row')"))
    await started.wait()
    try:
        async with asyncio.timeout(1):
            async with db.execute("SELECT count(*) FROM exercises") as cur:
                await cur.fetchone()
    finally:
        release.set()
        await tx


def test_transactions_are_only_opened_by_write_tx():
    """A hand-rolled BEGIN would hold write_lock outside write_tx, and the
    write gate would then deadlock on it. db.py (write_tx, init_db) is the
    only place allowed to open one."""
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "db.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"""["']\s*BEGIN\b|write_lock""", line):
                offenders.append(f"{path.relative_to(APP)}:{n}: {line.strip()}")
    assert offenders == []
