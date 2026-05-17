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


@pytest.mark.asyncio
async def test_workout_list_shows_resume_when_active(client):
    await client.post("/workouts", json={"notes": None})
    resp = await client.get("/workouts", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert b"Resume Session" in resp.content


@pytest.mark.asyncio
async def test_workout_list_shows_start_when_no_active(client):
    resp = await client.get("/workouts", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert b"Start Session" in resp.content
    assert b"Resume Session" not in resp.content


@pytest.mark.asyncio
async def test_dashboard_shows_active_session_card(client):
    await client.post("/workouts", json={"notes": None})
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert b"Session in progress" in resp.content
    assert b"Resume Session" in resp.content


@pytest.mark.asyncio
async def test_dashboard_no_active_session_shows_start(client):
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert b"Start Session" in resp.content
    assert b"Session in progress" not in resp.content


@pytest.mark.asyncio
async def test_repeat_workout_exercise_order(client):
    ex1 = await client.post("/exercises", json={"name": "Repeat Test A"})
    ex2 = await client.post("/exercises", json={"name": "Repeat Test B"})
    ex1_id, ex2_id = ex1.json()["id"], ex2.json()["id"]

    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    # Log A, then B, then A again — endpoint should return [A, B] (deduplicated, original order)
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex1_id, "reps": 5, "weight_kg": 100.0})
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex2_id, "reps": 3, "weight_kg": 140.0})
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex1_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.get(f"/api/workouts/{w_id}/exercises")
    assert resp.status_code == 200
    exercises = resp.json()
    assert [e["id"] for e in exercises] == [ex1_id, ex2_id]
    assert exercises[0]["name"] == "Repeat Test A"
    assert exercises[1]["name"] == "Repeat Test B"


@pytest.mark.asyncio
async def test_repeat_workout_404_for_missing(client):
    resp = await client.get("/api/workouts/99999/exercises")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_log_set_with_rpe(client):
    ex = await client.post("/exercises", json={"name": "RPE Test Lift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    resp = await client.post(f"/workouts/{w_id}/sets",
                             json={"exercise_id": ex_id, "reps": 5,
                                   "weight_kg": 100.0, "rpe": 8})
    assert resp.status_code == 201

    page = await client.get(f"/workouts/{w_id}", headers={"Accept": "text/html"})
    assert "RPE 8" in page.text


@pytest.mark.asyncio
async def test_log_set_rpe_optional(client):
    ex = await client.post("/exercises", json={"name": "RPE Optional Test"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    resp = await client.post(f"/workouts/{w_id}/sets",
                             json={"exercise_id": ex_id, "reps": 5, "weight_kg": 80.0})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_log_set_rpe_out_of_range(client):
    ex = await client.post("/exercises", json={"name": "RPE Range Test"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    resp = await client.post(f"/workouts/{w_id}/sets",
                             json={"exercise_id": ex_id, "reps": 5,
                                   "weight_kg": 80.0, "rpe": 11})
    assert resp.status_code == 422
