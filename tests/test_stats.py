import pytest
from datetime import datetime, date, timedelta


@pytest.mark.asyncio
async def test_stats_renders(client):
    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Stats" in resp.text
    assert "Weekly volume" in resp.text
    assert "Top exercises" in resp.text
    assert "Muscle coverage" in resp.text


@pytest.mark.asyncio
async def test_stats_empty_state(client):
    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "training arc appears here" in resp.text
    assert "No sets logged yet" in resp.text
    assert "No workouts logged this week" in resp.text


@pytest.mark.asyncio
async def test_stats_sparkline_empty_state(client):
    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "training arc appears here" in resp.text
    assert "Start a session" in resp.text
    assert "<svg" not in resp.text


@pytest.mark.asyncio
async def test_stats_shows_top_exercise(client, db):
    ex = await client.post("/exercises", json={"name": "Stats Test Lift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Stats Test Lift" in resp.text


@pytest.mark.asyncio
async def test_stats_shows_sparkline_with_two_weeks(client, db):
    ex = await client.post("/exercises", json={"name": "Stats Sparkline Lift"})
    ex_id = ex.json()["id"]

    # Two workouts in different weeks
    for days_ago in (14, 0):
        started = (datetime.now() - timedelta(days=days_ago)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, started_at) VALUES (1, ?)", (started,)
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM workouts WHERE started_at = ?", (started,)
        ) as cur:
            w_id = (await cur.fetchone())["id"]
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,5,80.0,1)",
            (w_id, ex_id),
        )
        await db.commit()

    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "<svg" in resp.text


@pytest.mark.asyncio
async def test_stats_shows_plateau(client, db):
    ex = await client.post("/exercises", json={"name": "Plateau Test Lift"})
    ex_id = ex.json()["id"]

    # Three sessions: two old (>21 days) at 100 kg, one recent at same weight
    for days_ago in (40, 28, 5):
        started = (datetime.now() - timedelta(days=days_ago)).isoformat()
        ended = (datetime.now() - timedelta(days=days_ago - 1)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
            (started, ended),
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM workouts WHERE started_at = ?", (started,)
        ) as cur:
            w_id = (await cur.fetchone())["id"]
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,5,100.0,1)",
            (w_id, ex_id),
        )
        await db.commit()

    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Plateau Test Lift" in resp.text
    assert "no progress" in resp.text.lower()


@pytest.mark.asyncio
async def test_stats_no_plateau_when_improving(client, db):
    ex = await client.post("/exercises", json={"name": "Improving Lift"})
    ex_id = ex.json()["id"]

    # Old session at 80 kg, recent session at 90 kg — no plateau
    for days_ago, weight in [(30, 80.0), (3, 90.0)]:
        started = (datetime.now() - timedelta(days=days_ago)).isoformat()
        ended = (datetime.now() - timedelta(days=days_ago - 1)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
            (started, ended),
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM workouts WHERE started_at = ?", (started,)
        ) as cur:
            w_id = (await cur.fetchone())["id"]
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,5,?,1)",
            (w_id, ex_id, weight),
        )
        await db.commit()

    resp = await client.get("/stats", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "no progress" not in resp.text.lower()
