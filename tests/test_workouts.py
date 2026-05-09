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
    ex = await client.post("/exercises", json={"name": "Test OH Press"})
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
async def test_patch_workout_notes(client):
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    resp = await client.patch(f"/workouts/{w_id}", json={"notes": "Felt strong today"})
    assert resp.status_code == 204

    detail = await client.get(f"/workouts/{w_id}", headers={"Accept": "application/json"})
    assert detail.status_code == 200


@pytest.mark.asyncio
async def test_patch_missing_workout_returns_404(client):
    resp = await client.patch("/workouts/99999", json={"notes": "ghost"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_finish_workout_returns_summary(client):
    ex = await client.post("/exercises", json={"name": "Test Deadlift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.post(f"/workouts/{w_id}/finish")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workout_id"] == w_id
    assert data["set_count"] == 1
    assert data["volume_kg"] == 500.0
    assert data["duration_minutes"] >= 0


@pytest.mark.asyncio
async def test_finish_workout_idempotent(client):
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/finish")
    resp = await client.post(f"/workouts/{w_id}/finish")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_finish_missing_workout_returns_404(client):
    resp = await client.post("/workouts/99999/finish")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_set_to_missing_workout(client):
    ex = await client.post("/exercises", json={"name": "Curl"})
    resp = await client.post("/workouts/99999/sets",
                             json={"exercise_id": ex.json()["id"], "reps": 10, "weight_kg": 20.0})
    assert resp.status_code == 404
