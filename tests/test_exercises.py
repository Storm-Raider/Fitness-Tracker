import pytest


@pytest.mark.asyncio
async def test_create_exercise(client):
    resp = await client.post("/exercises", json={"name": "Test Bench Press"})
    assert resp.status_code == 201
    assert "id" in resp.json()


@pytest.mark.asyncio
async def test_duplicate_exercise_409(client):
    await client.post("/exercises", json={"name": "Squat"})
    resp = await client.post("/exercises", json={"name": "Squat"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_exercises_includes_last_sets(client):
    # Create exercise and a workout + set
    ex = await client.post("/exercises", json={"name": "Test Deadlift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets", json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.get("/exercises")
    assert resp.status_code == 200
    data = resp.json()
    assert str(ex_id) in data["last_sets"]
    assert data["last_sets"][str(ex_id)]["weight_kg"] == 100.0
