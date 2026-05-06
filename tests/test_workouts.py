import pytest


@pytest.mark.asyncio
async def test_create_workout(client):
    resp = await client.post("/workouts", json={"notes": "Morning session"})
    assert resp.status_code == 201
    assert "id" in resp.json()


@pytest.mark.asyncio
async def test_list_workouts(client):
    await client.post("/workouts", json={"notes": None})
    resp = await client.get("/workouts", headers={"Accept": "application/json"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_add_set_and_pr_detection(client):
    ex = await client.post("/exercises", json={"name": "Overhead Press"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    # First set — always a PR (no prior)
    r1 = await client.post(f"/workouts/{w_id}/sets",
                           json={"exercise_id": ex_id, "reps": 5, "weight_kg": 60.0})
    assert r1.status_code == 201
    assert r1.json()["is_pr"] is True

    # Same weight — not a PR
    r2 = await client.post(f"/workouts/{w_id}/sets",
                           json={"exercise_id": ex_id, "reps": 5, "weight_kg": 60.0})
    assert r2.json()["is_pr"] is False

    # Heavier — PR
    r3 = await client.post(f"/workouts/{w_id}/sets",
                           json={"exercise_id": ex_id, "reps": 3, "weight_kg": 65.0})
    assert r3.json()["is_pr"] is True


@pytest.mark.asyncio
async def test_delete_set(client):
    ex = await client.post("/exercises", json={"name": "Row"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    s = await client.post(f"/workouts/{w_id}/sets",
                          json={"exercise_id": ex_id, "reps": 8, "weight_kg": 50.0})
    s_id = s.json()["id"]

    resp = await client.delete(f"/workouts/{w_id}/sets/{s_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_add_set_to_missing_workout(client):
    ex = await client.post("/exercises", json={"name": "Curl"})
    resp = await client.post("/workouts/99999/sets",
                             json={"exercise_id": ex.json()["id"], "reps": 10, "weight_kg": 20.0})
    assert resp.status_code == 404
