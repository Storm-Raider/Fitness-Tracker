import pytest


@pytest.mark.asyncio
async def test_api_exercises_includes_muscles_array(client):
    resp = await client.get("/api/exercises")
    assert resp.status_code == 200
    data = resp.json()
    bench = next((e for e in data["exercises"] if e["name"] == "Bench Press"), None)
    assert bench is not None
    assert "muscles" in bench
    assert isinstance(bench["muscles"], list)
    muscle_names = [m["name"] for m in bench["muscles"]]
    assert "Chest" in muscle_names


@pytest.mark.asyncio
async def test_exercise_muscles_seeded(client):
    resp = await client.get("/api/exercises")
    bench = next(e for e in resp.json()["exercises"] if e["name"] == "Bench Press")
    assert len(bench["muscles"]) >= 1
    primary = [m for m in bench["muscles"] if m["is_primary"]]
    assert len(primary) >= 1


@pytest.mark.asyncio
async def test_api_exercises_user_created_has_empty_muscles(client):
    resp = await client.post("/exercises", json={"name": "My Custom Move"})
    assert resp.status_code == 201
    ex_id = resp.json()["id"]

    resp = await client.get("/api/exercises")
    custom = next((e for e in resp.json()["exercises"] if e["id"] == ex_id), None)
    assert custom is not None
    assert custom["muscles"] == []


@pytest.mark.asyncio
async def test_exercise_detail_page_shows_muscles_from_join_table(client):
    resp = await client.get("/api/exercises")
    bench = next(e for e in resp.json()["exercises"] if e["name"] == "Bench Press")
    page = await client.get(f"/exercises/{bench['id']}")
    assert page.status_code == 200
    assert "Chest" in page.text


@pytest.mark.asyncio
async def test_exercise_detail_page_user_created_no_crash(client):
    ex = await client.post("/exercises", json={"name": "No Muscle Move"})
    ex_id = ex.json()["id"]
    page = await client.get(f"/exercises/{ex_id}")
    assert page.status_code == 200


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

    resp = await client.get("/api/exercises")
    assert resp.status_code == 200
    data = resp.json()
    assert str(ex_id) in data["last_sets"]
    assert data["last_sets"][str(ex_id)]["weight_kg"] == 100.0


@pytest.mark.asyncio
async def test_last_sets_returns_most_recent_set(client):
    ex = await client.post("/exercises", json={"name": "OL Squat"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 80.0})
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 3, "weight_kg": 90.0})

    data = (await client.get("/api/exercises")).json()
    last = data["last_sets"][str(ex_id)]
    assert last["weight_kg"] == 90.0
    assert last["reps"] == 3


@pytest.mark.asyncio
async def test_create_exercise_infers_muscle_from_name(client):
    """A custom exercise with a recognisable name gets a muscle group inferred."""
    resp = await client.post("/exercises", json={"name": "Testing Spider Curl"})
    assert resp.status_code == 201
    assert resp.json()["muscle"] == "Biceps"
    ex_id = resp.json()["id"]
    data = (await client.get("/api/exercises")).json()
    custom = next(e for e in data["exercises"] if e["id"] == ex_id)
    assert [m["name"] for m in custom["muscles"]] == ["Biceps"]


@pytest.mark.asyncio
async def test_create_exercise_explicit_muscle_overrides_inference(client):
    """An explicit muscle_primary wins over name inference."""
    resp = await client.post("/exercises", json={"name": "Mystery Press", "muscle_primary": "Chest"})
    assert resp.status_code == 201
    assert resp.json()["muscle"] == "Chest"


@pytest.mark.asyncio
async def test_create_exercise_invalid_muscle_falls_back_to_inference(client):
    """A bogus muscle is ignored; the name inference is used instead."""
    resp = await client.post("/exercises", json={"name": "Hammer Curl Variation", "muscle_primary": "Bogus"})
    assert resp.status_code == 201
    assert resp.json()["muscle"] == "Biceps"


@pytest.mark.asyncio
async def test_api_exercises_flags_bodyweight(client):
    """/api/exercises marks bodyweight exercises so the form can switch to the
    'added weight' input."""
    data = (await client.get("/api/exercises")).json()
    pushup = next((e for e in data["exercises"] if e["name"] == "Push-up"), None)
    bench = next((e for e in data["exercises"] if e["name"] == "Bench Press"), None)
    assert pushup is not None and pushup["is_bodyweight"] is True
    assert bench is not None and bench["is_bodyweight"] is False
