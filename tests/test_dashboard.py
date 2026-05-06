import pytest


@pytest.mark.asyncio
async def test_dashboard_renders(client):
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "FitTrack" in resp.text


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
