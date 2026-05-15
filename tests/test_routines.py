import pytest


@pytest.mark.asyncio
async def test_global_routines_visible_to_user(client):
    resp = await client.get("/routines")
    assert resp.status_code == 200
    assert len(resp.json()) == 14


@pytest.mark.asyncio
async def test_create_and_list_routine(client):
    ex1 = await client.post("/exercises", json={"name": "Test Bench Press"})
    ex2 = await client.post("/exercises", json={"name": "Test OH Press"})
    ex_ids = [ex1.json()["id"], ex2.json()["id"]]

    resp = await client.post("/routines", json={"name": "Push Day", "exercise_ids": ex_ids})
    assert resp.status_code == 201
    r_id = resp.json()["id"]

    listing = await client.get("/routines")
    routines = listing.json()
    assert len(routines) == 15
    push_day = next(r for r in routines if r["name"] == "Push Day")
    assert push_day["id"] == r_id
    assert [e["name"] for e in push_day["exercises"]] == ["Test Bench Press", "Test OH Press"]


@pytest.mark.asyncio
async def test_create_routine_empty_name_rejected(client):
    ex = await client.post("/exercises", json={"name": "Squat"})
    resp = await client.post("/routines",
                             json={"name": "", "exercise_ids": [ex.json()["id"]]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_routine_no_exercises_rejected(client):
    resp = await client.post("/routines", json={"name": "Empty", "exercise_ids": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_routine(client):
    ex = await client.post("/exercises", json={"name": "Pull Up"})
    r = await client.post("/routines",
                          json={"name": "Pull Day", "exercise_ids": [ex.json()["id"]]})
    r_id = r.json()["id"]

    resp = await client.delete(f"/routines/{r_id}")
    assert resp.status_code == 204

    listing = await client.get("/routines")
    assert len(listing.json()) == 14


@pytest.mark.asyncio
async def test_delete_missing_routine_returns_404(client):
    resp = await client.delete("/routines/99999")
    assert resp.status_code == 404
