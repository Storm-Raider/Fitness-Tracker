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
    assert resp.status_code == 200
    assert resp.headers.get("X-Undo-Token")  # delete is now recoverable


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
async def test_patch_set_updates_weight_and_reps(client, db):
    ex = await client.post("/exercises", json={"name": "Test Bench Press Edit"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    s = await client.post(f"/workouts/{w_id}/sets",
                          json={"exercise_id": ex_id, "reps": 5, "weight_kg": 60.0})
    s_id = s.json()["id"]

    r = await client.patch(f"/workouts/{w_id}/sets/{s_id}",
                           json={"reps": 8, "weight_kg": 65.0, "notes": "felt strong", "rpe": 8})
    assert r.status_code == 200
    assert r.json()["weight_kg"] == 65.0
    assert r.json()["reps"] == 8
    assert r.json()["notes"] == "felt strong"

    async with db.execute("SELECT reps, weight_kg, notes FROM sets WHERE id=?", (s_id,)) as c:
        row = await c.fetchone()
    assert row["weight_kg"] == 65.0
    assert row["reps"] == 8
    assert row["notes"] == "felt strong"


@pytest.mark.asyncio
async def test_patch_set_wrong_workout_returns_404(client):
    ex = await client.post("/exercises", json={"name": "Test Squat Edit"})
    ex_id = ex.json()["id"]
    w1 = await client.post("/workouts", json={"notes": None})
    w2 = await client.post("/workouts", json={"notes": None})
    s = await client.post(f"/workouts/{w1.json()['id']}/sets",
                          json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})
    s_id = s.json()["id"]

    # Try to patch set from w1 using w2's ID — should 404
    r = await client.patch(f"/workouts/{w2.json()['id']}/sets/{s_id}",
                           json={"reps": 5, "weight_kg": 105.0})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_set_invalid_reps_returns_422(client):
    ex = await client.post("/exercises", json={"name": "Test Deadlift Edit"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    s = await client.post(f"/workouts/{w_id}/sets",
                          json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})
    s_id = s.json()["id"]

    r = await client.patch(f"/workouts/{w_id}/sets/{s_id}",
                           json={"reps": 0, "weight_kg": 100.0})
    assert r.status_code == 422


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


# ── Workout list search ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_by_exercise_name(client, db_conn):
    ex = await client.post("/exercises", json={"name": "Search Test Deadlift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})
    # Finish so it shows up in the completed list
    await db_conn.execute("UPDATE workouts SET ended_at=datetime('now','localtime') WHERE id=?", (w_id,))
    await db_conn.commit()

    r = await client.get("/workouts?q=search+test+deadlift", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Search Test Deadlift" in r.text or "1 result" in r.text

    r2 = await client.get("/workouts?q=nonexistent+exercise+xyz", headers={"Accept": "text/html"})
    assert r2.status_code == 200
    assert "No workouts match" in r2.text


@pytest.mark.asyncio
async def test_search_by_notes(client, db_conn):
    w = await client.post("/workouts", json={"notes": "heavy Monday"})
    w_id = w.json()["id"]
    await db_conn.execute("UPDATE workouts SET ended_at=datetime('now','localtime') WHERE id=?", (w_id,))
    await db_conn.commit()
    # Seed a set so the workout survives the HAVING clause
    ex = await client.post("/exercises", json={"name": "Press"})
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex.json()["id"], "reps": 3, "weight_kg": 50.0})

    r = await client.get("/workouts?q=monday", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "heavy Monday" in r.text


@pytest.mark.asyncio
async def test_search_empty_query_returns_all(client, db_conn):
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    ex = await client.post("/exercises", json={"name": "Row"})
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex.json()["id"], "reps": 8, "weight_kg": 60.0})
    await db_conn.execute("UPDATE workouts SET ended_at=datetime('now','localtime') WHERE id=?", (w_id,))
    await db_conn.commit()

    r = await client.get("/workouts?q=", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "No workouts match" not in r.text


# ── Bodyweight sets ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bodyweight_set_stores_added_weight(client, db_conn):
    ex = await client.post("/exercises", json={"name": "BW Test Pushup"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    # Effective load = bodyweight(75) + added(10) = 85, added = 10
    r = await client.post(f"/workouts/{w_id}/sets",
                          json={"exercise_id": ex_id, "reps": 12, "weight_kg": 85.0, "added_weight_kg": 10.0})
    assert r.status_code == 201
    assert r.json()["added_weight_kg"] == 10.0
    async with db_conn.execute("SELECT weight_kg, added_weight_kg FROM sets WHERE id=?", (r.json()["id"],)) as c:
        row = await c.fetchone()
    assert row["weight_kg"] == 85.0          # effective load drives volume
    assert row["added_weight_kg"] == 10.0    # flags it as a bodyweight set


@pytest.mark.asyncio
async def test_regular_set_has_null_added_weight(client, db_conn):
    ex = await client.post("/exercises", json={"name": "BW Test Bench"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    r = await client.post(f"/workouts/{w_id}/sets",
                          json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})
    assert r.status_code == 201
    assert r.json()["added_weight_kg"] is None
    async with db_conn.execute("SELECT added_weight_kg FROM sets WHERE id=?", (r.json()["id"],)) as c:
        assert (await c.fetchone())["added_weight_kg"] is None


# ── Timed-hold sets (planks) ─────────────────────────────────────────────────

async def _plank_id(db_conn):
    async with db_conn.execute("SELECT id FROM exercises WHERE name='Plank'") as c:
        return (await c.fetchone())["id"]


@pytest.mark.asyncio
async def test_time_set_stores_duration_and_zero_reps(client, db_conn):
    ex_id = await _plank_id(db_conn)
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    # 60s plank at bodyweight 80kg; reps placeholder 1, server stores 0
    r = await client.post(f"/workouts/{w_id}/sets",
                          json={"exercise_id": ex_id, "reps": 1, "weight_kg": 80.0,
                                "added_weight_kg": 0.0, "duration_seconds": 60})
    assert r.status_code == 201
    assert r.json()["duration_seconds"] == 60
    assert r.json()["is_pr"] is False          # holds don't make weight PRs
    async with db_conn.execute("SELECT reps, duration_seconds FROM sets WHERE id=?", (r.json()["id"],)) as c:
        row = await c.fetchone()
    assert row["reps"] == 0                      # excluded from weight×reps volume
    assert row["duration_seconds"] == 60


@pytest.mark.asyncio
async def test_time_set_excluded_from_volume(client, db_conn):
    ex_id = await _plank_id(db_conn)
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 1, "weight_kg": 80.0, "duration_seconds": 45})
    # Workout volume should be 0 (reps=0 for the hold)
    async with db_conn.execute(
        "SELECT COALESCE(SUM(weight_kg*reps),0) AS vol FROM sets WHERE workout_id=?", (w_id,)
    ) as c:
        assert (await c.fetchone())["vol"] == 0


@pytest.mark.asyncio
async def test_api_exercises_flags_time(client):
    data = (await client.get("/api/exercises")).json()
    plank = next((e for e in data["exercises"] if e["name"] == "Plank"), None)
    bench = next((e for e in data["exercises"] if e["name"] == "Bench Press"), None)
    assert plank is not None and plank["is_time"] is True
    assert bench is not None and bench["is_time"] is False
