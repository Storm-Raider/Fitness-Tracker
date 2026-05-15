import pytest


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_page_renders(client):
    resp = await client.get("/settings")
    assert resp.status_code == 200
    assert b"Account Settings" in resp.content


@pytest.mark.asyncio
async def test_settings_update_email_success(client, db_conn):
    resp = await client.post("/settings/email", data={"email": "new@example.com"})
    assert resp.status_code in (302, 303)
    assert "success=email" in resp.headers["location"]

    async with db_conn.execute("SELECT email FROM users WHERE id = 1") as cur:
        row = await cur.fetchone()
    assert row["email"] == "new@example.com"


@pytest.mark.asyncio
async def test_settings_update_email_invalid(client):
    resp = await client.post("/settings/email", data={"email": "not-valid"})
    assert resp.status_code == 200
    assert b"valid email" in resp.content.lower()


@pytest.mark.asyncio
async def test_settings_update_email_duplicate(client, db_conn):
    await db_conn.execute(
        "INSERT INTO users(username, password_hash, email) VALUES ('other', 'x', 'taken@example.com')"
    )
    await db_conn.commit()

    resp = await client.post("/settings/email", data={"email": "taken@example.com"})
    assert resp.status_code == 200
    assert b"already exists" in resp.content.lower()


@pytest.mark.asyncio
async def test_settings_change_password_success(client, db_conn):
    resp = await client.post("/settings/password", data={
        "current_password": "test-password",
        "new_password": "brandnewpass1",
        "new_password_confirm": "brandnewpass1",
    })
    assert resp.status_code in (302, 303)
    assert "success=password" in resp.headers["location"]


@pytest.mark.asyncio
async def test_settings_change_password_wrong_current(client):
    resp = await client.post("/settings/password", data={
        "current_password": "wrongpassword",
        "new_password": "newpassword1",
        "new_password_confirm": "newpassword1",
    })
    assert resp.status_code == 200
    assert b"incorrect" in resp.content.lower()


@pytest.mark.asyncio
async def test_settings_change_password_too_short(client):
    resp = await client.post("/settings/password", data={
        "current_password": "test-password",
        "new_password": "short",
        "new_password_confirm": "short",
    })
    assert resp.status_code == 200
    assert b"8" in resp.content


@pytest.mark.asyncio
async def test_settings_change_password_mismatch(client):
    resp = await client.post("/settings/password", data={
        "current_password": "test-password",
        "new_password": "newpassword1",
        "new_password_confirm": "newpassword2",
    })
    assert resp.status_code == 200
    assert b"do not match" in resp.content.lower()


# ---------------------------------------------------------------------------
# Delete workout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_workout_removes_workout_and_sets(client, db_conn):
    ex = await client.post("/exercises", json={"name": "Delete Test Ex"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 50.0})

    resp = await client.delete(f"/workouts/{w_id}")
    assert resp.status_code == 200

    async with db_conn.execute("SELECT id FROM workouts WHERE id = ?", (w_id,)) as cur:
        assert await cur.fetchone() is None
    async with db_conn.execute("SELECT id FROM sets WHERE workout_id = ?", (w_id,)) as cur:
        assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_delete_workout_other_user_returns_404(client, user_b_client):
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    resp = await user_b_client.delete(f"/workouts/{w_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_workout_returns_404(client):
    resp = await client.delete("/workouts/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete metric
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_metric_removes_entry(client, db_conn):
    resp = await client.post("/metrics", json={"weight_kg": 75.0, "calories": 2000})
    assert resp.status_code == 201
    metric_id = resp.json()["id"]

    del_resp = await client.delete(f"/metrics/{metric_id}")
    assert del_resp.status_code == 200

    async with db_conn.execute("SELECT id FROM body_metrics WHERE id = ?", (metric_id,)) as cur:
        assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_delete_metric_other_user_returns_404(client, user_b_client, db_conn):
    resp = await client.post("/metrics", json={"weight_kg": 80.0})
    metric_id = resp.json()["id"]

    del_resp = await user_b_client.delete(f"/metrics/{metric_id}")
    assert del_resp.status_code == 404


# ---------------------------------------------------------------------------
# Exercises browser page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exercises_page_renders_html(client):
    resp = await client.get("/exercises")
    assert resp.status_code == 200
    assert b"Exercises" in resp.content
    assert b"<html" in resp.content


@pytest.mark.asyncio
async def test_api_exercises_returns_json(client):
    resp = await client.get("/api/exercises")
    assert resp.status_code == 200
    data = resp.json()
    assert "exercises" in data
    assert "last_sets" in data
