import pytest
from datetime import datetime, timedelta


@pytest.mark.asyncio
async def test_dashboard_renders(client):
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "FitStorm" in resp.text


@pytest.mark.asyncio
async def test_dashboard_shows_streak(client):
    ex = await client.post("/exercises", json={"name": "Squat"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "streak" in resp.text.lower()


@pytest.mark.asyncio
async def test_dashboard_json_response(client):
    resp = await client.get("/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_stat_cards_present(client):
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Total workouts" in resp.text
    assert "Total volume" in resp.text
    assert "Avg duration" in resp.text
    assert "Best streak" in resp.text


@pytest.mark.asyncio
async def test_dashboard_avg_duration_none_renders_dash(client):
    # No workouts with ended_at — avg_duration_min is None; template must show "—" not "None"
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "—" in resp.text
    assert "None" not in resp.text


@pytest.mark.asyncio
async def test_dashboard_avg_duration_shows_minutes(client, db):
    started = datetime.now() - timedelta(minutes=35)
    ended = datetime.now()
    await db.execute(
        "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
        (started.isoformat(), ended.isoformat()),
    )
    await db.commit()
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "min" in resp.text
