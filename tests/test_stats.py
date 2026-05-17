import pytest
from datetime import datetime, timedelta


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
