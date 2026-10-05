"""write_tx and the neutralised conn.commit: atomicity on the one shared connection."""
import asyncio
import json

import pytest

import app.db as appdb
from app.db import WriteConflict, write_tx


async def _count(conn, table="sessions_probe"):
    async with conn.execute(f"SELECT COUNT(*) FROM {table}") as c:
        return (await c.fetchone())[0]


@pytest.fixture
async def probe(db_conn):
    await db_conn.execute("CREATE TABLE sessions_probe(v TEXT)")
    return db_conn


async def test_commit_is_a_noop_and_does_not_end_a_transaction(probe):
    """A foreign conn.commit() must not end another request's open write_tx."""
    async with write_tx(probe):
        await probe.execute("INSERT INTO sessions_probe VALUES ('a')")
        await probe.commit()  # what ~54 route handlers do
        assert probe.in_transaction
        await probe.execute("INSERT INTO sessions_probe VALUES ('b')")
    assert not probe.in_transaction
    assert await _count(probe) == 2


async def test_exception_rolls_back_everything(probe):
    with pytest.raises(ValueError):
        async with write_tx(probe):
            await probe.execute("INSERT INTO sessions_probe VALUES ('a')")
            await probe.commit()  # foreign commit must not make 'a' durable
            raise ValueError("boom")
    assert not probe.in_transaction
    assert await _count(probe) == 0


async def test_write_conflict_commits_and_reraises(probe):
    await probe.execute("INSERT INTO sessions_probe VALUES ('keep')")
    with pytest.raises(WriteConflict):
        async with write_tx(probe):
            raise WriteConflict("stale")
    assert not probe.in_transaction
    assert await _count(probe) == 1
    async with write_tx(probe):  # lock was released
        await probe.execute("INSERT INTO sessions_probe VALUES ('next')")
    assert await _count(probe) == 2


async def test_nested_write_tx_raises_instead_of_deadlocking(probe):
    async with write_tx(probe):
        with pytest.raises(RuntimeError, match="nested write_tx"):
            async with write_tx(probe):
                pass
        await probe.execute("INSERT INTO sessions_probe VALUES ('outer')")
    assert await _count(probe) == 1
    async with write_tx(probe):  # flag was reset
        pass


async def test_transactions_serialise(probe):
    order = []

    async def writer(name, hold):
        async with write_tx(probe):
            order.append(f"{name}:start")
            await probe.execute("INSERT INTO sessions_probe VALUES (?)", (name,))
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    await asyncio.gather(writer("a", 0.05), writer("b", 0))
    assert order in (["a:start", "a:end", "b:start", "b:end"],
                     ["b:start", "b:end", "a:start", "a:end"])
    assert await _count(probe) == 2


async def test_begin_failure_releases_the_lock(probe, monkeypatch):
    real = probe.execute

    async def failing(sql, *a, **k):
        if sql == "BEGIN IMMEDIATE":
            raise RuntimeError("database is locked")
        return await real(sql, *a, **k)

    monkeypatch.setattr(probe, "execute", failing)
    with pytest.raises(RuntimeError, match="database is locked"):
        async with write_tx(probe):
            pass
    monkeypatch.undo()
    assert not appdb.write_lock.locked()
    async with write_tx(probe):
        pass


async def _seed_draft(conn, uid=1):
    async with conn.execute("SELECT id FROM exercises ORDER BY id LIMIT 2") as c:
        ids = [r["id"] for r in await c.fetchall()]
    plan = {"title": "T", "days": [{"focus": "Push", "exercises": [
        {"exercise_id": ids[0], "name": "x", "sets": 3, "reps": "8"},
        {"exercise_id": ids[1], "name": "y", "sets": 3, "reps": "8"}]}]}
    async with conn.execute(
        "INSERT INTO coach_plans(user_id,title,goal,days_per_week,plan_json,status) "
        "VALUES (?,?,?,?,?,'draft')", (uid, "T", "hypertrophy", 1, json.dumps(plan))) as c:
        return c.lastrowid


async def test_concurrent_confirms_create_routines_once(client, db_conn):
    pid = await _seed_draft(db_conn)
    r1, r2 = await asyncio.gather(
        client.post(f"/coach/plans/{pid}/confirm"),
        client.post(f"/coach/plans/{pid}/confirm"),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    async with db_conn.execute("SELECT COUNT(*) FROM routines WHERE user_id=1") as c:
        assert (await c.fetchone())[0] == 1
    async with db_conn.execute("SELECT status FROM coach_plans WHERE id=?", (pid,)) as c:
        assert (await c.fetchone())["status"] == "saved"


async def test_confirm_twice_sequentially_is_404_then_unchanged(client, db_conn):
    pid = await _seed_draft(db_conn)
    assert (await client.post(f"/coach/plans/{pid}/confirm")).status_code == 201
    assert (await client.post(f"/coach/plans/{pid}/confirm")).status_code == 404
    async with db_conn.execute("SELECT COUNT(*) FROM routines WHERE user_id=1") as c:
        assert (await c.fetchone())[0] == 1


async def test_route_commit_during_open_write_tx_keeps_it_open(client, db_conn):
    """Interleaving: a route that calls conn.commit() mid-transaction is harmless."""
    pid = await _seed_draft(db_conn)
    async with write_tx(db_conn):
        await db_conn.execute("INSERT INTO sessions(id,user_id,expires_at) VALUES "
                              "('s-x',1,datetime('now','+1 day'))")
        # the feedback route ends with conn.commit() (now a no-op); it must not
        # close our transaction. It would block on write_lock if it used write_tx,
        # so only assert the direct commit here.
        await db_conn.commit()
        assert db_conn.in_transaction
    assert not db_conn.in_transaction


async def test_stale_draft_purge_uses_localtime_and_keeps_fresh(client, db_conn):
    fresh = await _seed_draft(db_conn)
    stale = await _seed_draft(db_conn)
    await db_conn.execute(
        "UPDATE coach_plans SET created_at=datetime('now','localtime','-8 days') WHERE id=?", (stale,))
    r = await client.get("/plan")
    assert r.status_code == 200
    async with db_conn.execute("SELECT id FROM coach_plans ORDER BY id") as c:
        assert [row["id"] for row in await c.fetchall()] == [fresh]


async def test_delete_plan_removes_its_routines_atomically(client, db_conn):
    pid = await _seed_draft(db_conn)
    assert (await client.post(f"/coach/plans/{pid}/confirm")).status_code == 201
    assert (await client.delete(f"/coach/plans/{pid}")).status_code == 204
    async with db_conn.execute("SELECT COUNT(*) FROM routines WHERE user_id=1") as c:
        assert (await c.fetchone())[0] == 0
    async with db_conn.execute("SELECT COUNT(*) FROM coach_plans") as c:
        assert (await c.fetchone())[0] == 0
