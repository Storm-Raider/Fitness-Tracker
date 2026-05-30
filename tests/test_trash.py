import pytest


async def _cardio_ex(db):
    async with db.execute("SELECT id FROM exercises WHERE category='Cardio' LIMIT 1") as c:
        return (await c.fetchone())["id"]


async def _new_set(client, db, name, reps=5, weight=100.0):
    ex = (await client.post("/exercises", json={"name": name})).json()["id"]
    w = (await client.post("/workouts", json={"notes": None})).json()["id"]
    s = (await client.post(f"/workouts/{w}/sets",
                           json={"exercise_id": ex, "reps": reps, "weight_kg": weight})).json()["id"]
    return ex, w, s


@pytest.mark.asyncio
async def test_delete_set_then_undo(client, db):
    ex, w, s = await _new_set(client, db, "Trash Lift")

    r = await client.delete(f"/workouts/{w}/sets/{s}")
    assert r.status_code == 200
    token = r.headers.get("X-Undo-Token")
    assert token, "delete must return an undo token header"
    # gone from the live table (so it can't taint stats/PRs)
    async with db.execute("SELECT COUNT(*) AS n FROM sets WHERE id=?", (s,)) as c:
        assert (await c.fetchone())["n"] == 0

    u = await client.post(f"/undo/{token}")
    assert u.status_code == 200
    async with db.execute(
        "SELECT COUNT(*) AS n FROM sets WHERE workout_id=? AND exercise_id=?", (w, ex)
    ) as c:
        assert (await c.fetchone())["n"] == 1  # restored (new id)


@pytest.mark.asyncio
async def test_undo_token_is_single_use(client, db):
    _, w, s = await _new_set(client, db, "Single Use Lift")
    token = (await client.delete(f"/workouts/{w}/sets/{s}")).headers["X-Undo-Token"]
    assert (await client.post(f"/undo/{token}")).status_code == 200
    assert (await client.post(f"/undo/{token}")).status_code == 404  # consumed


@pytest.mark.asyncio
async def test_delete_workout_then_undo_restores_bundle(client, db):
    ex = (await client.post("/exercises", json={"name": "Bundle Lift"})).json()["id"]
    cardio_ex = await _cardio_ex(db)
    w = (await client.post("/workouts", json={"notes": "bundle-marker"})).json()["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 100.0})
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 105.0})
    await client.post(f"/workouts/{w}/cardio", json={"exercise_id": cardio_ex, "duration_minutes": 20.0})

    r = await client.delete(f"/workouts/{w}")
    assert r.status_code == 200
    token = r.headers["X-Undo-Token"]
    async with db.execute(
        "SELECT (SELECT COUNT(*) FROM workouts WHERE id=?) AS w, "
        "(SELECT COUNT(*) FROM sets WHERE workout_id=?) AS s, "
        "(SELECT COUNT(*) FROM cardio_logs WHERE workout_id=?) AS c", (w, w, w)
    ) as cur:
        row = await cur.fetchone()
    assert (row["w"], row["s"], row["c"]) == (0, 0, 0)

    assert (await client.post(f"/undo/{token}")).status_code == 200
    async with db.execute("SELECT id FROM workouts WHERE notes='bundle-marker'") as cur:
        restored = [r2["id"] for r2 in await cur.fetchall()]
    assert len(restored) == 1
    nw = restored[0]
    async with db.execute("SELECT COUNT(*) AS n FROM sets WHERE workout_id=?", (nw,)) as c:
        assert (await c.fetchone())["n"] == 2
    async with db.execute("SELECT COUNT(*) AS n FROM cardio_logs WHERE workout_id=?", (nw,)) as c:
        assert (await c.fetchone())["n"] == 1


@pytest.mark.asyncio
async def test_delete_standalone_cardio_then_undo(client, db):
    cardio_ex = await _cardio_ex(db)
    r = await client.post("/cardio", data={"exercise_id": cardio_ex, "duration_minutes": "30", "distance_km": "5"})
    assert r.status_code == 303
    async with db.execute("SELECT id FROM cardio_logs ORDER BY id DESC LIMIT 1") as c:
        cid = (await c.fetchone())["id"]

    dr = await client.delete(f"/cardio/{cid}")
    assert dr.status_code == 200
    token = dr.headers["X-Undo-Token"]
    async with db.execute("SELECT COUNT(*) AS n FROM cardio_logs WHERE id=?", (cid,)) as c:
        assert (await c.fetchone())["n"] == 0

    assert (await client.post(f"/undo/{token}")).status_code == 200
    async with db.execute(
        "SELECT COUNT(*) AS n FROM cardio_logs WHERE exercise_id=? AND duration_minutes=30.0", (cardio_ex,)
    ) as c:
        assert (await c.fetchone())["n"] == 1


@pytest.mark.asyncio
async def test_undo_unknown_token_404(client):
    assert (await client.post("/undo/does-not-exist")).status_code == 404


@pytest.mark.asyncio
async def test_undo_isolated_between_users(client, user_b_client, db):
    _, w, s = await _new_set(client, db, "Iso Lift", weight=50.0)
    token = (await client.delete(f"/workouts/{w}/sets/{s}")).headers["X-Undo-Token"]
    # user B cannot restore user A's deletion
    assert (await user_b_client.post(f"/undo/{token}")).status_code == 404
    # owner still can
    assert (await client.post(f"/undo/{token}")).status_code == 200


@pytest.mark.asyncio
async def test_undo_set_conflicts_when_workout_gone(client, db):
    _, w, s = await _new_set(client, db, "Orphan Lift", weight=60.0)
    set_token = (await client.delete(f"/workouts/{w}/sets/{s}")).headers["X-Undo-Token"]
    await client.delete(f"/workouts/{w}")  # parent workout now gone
    r = await client.post(f"/undo/{set_token}")
    assert r.status_code == 409  # can't restore an orphaned set


@pytest.mark.asyncio
async def test_old_trash_is_purged(client, db):
    await db.execute(
        "INSERT INTO deleted_items(token, user_id, kind, label, payload, deleted_at) "
        "VALUES ('stale-token', 1, 'set', 'old', '{}', datetime('now','localtime','-30 days'))"
    )
    await db.commit()
    # any fresh delete triggers opportunistic purge of the user's old trash
    _, w, s = await _new_set(client, db, "Purge Lift", weight=40.0)
    await client.delete(f"/workouts/{w}/sets/{s}")
    async with db.execute("SELECT COUNT(*) AS n FROM deleted_items WHERE token='stale-token'") as c:
        assert (await c.fetchone())["n"] == 0
