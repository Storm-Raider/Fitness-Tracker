import pytest


@pytest.mark.asyncio
async def test_routines_include_muscles_array(client):
    resp = await client.get("/routines")
    routines = resp.json()
    push_day = next((r for r in routines if "Push" in r["name"]), None)
    assert push_day is not None
    bench = next((e for e in push_day["exercises"] if e["name"] == "Bench Press"), None)
    assert bench is not None
    assert "muscles" in bench
    assert any(m["name"] == "Chest" for m in bench["muscles"])


@pytest.mark.asyncio
async def test_routines_exercise_without_muscles_has_empty_array(client):
    # A name with no recognisable keyword stays muscle-less (inference returns none).
    ex = await client.post("/exercises", json={"name": "Zzz Widget Move"})
    ex_id = ex.json()["id"]
    r = await client.post("/routines", json={"name": "Custom Routine", "exercise_ids": [ex_id]})
    r_id = r.json()["id"]

    resp = await client.get("/routines")
    routines = resp.json()
    routine = next(r for r in routines if r["id"] == r_id)
    custom_ex = next(e for e in routine["exercises"] if e["id"] == ex_id)
    assert custom_ex["muscles"] == []


@pytest.mark.asyncio
async def test_global_routines_visible_to_user(client):
    resp = await client.get("/routines")
    assert resp.status_code == 200
    assert len(resp.json()) == 12


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
    assert len(routines) == 13
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
    assert len(listing.json()) == 12


@pytest.mark.asyncio
async def test_delete_missing_routine_returns_404(client):
    resp = await client.delete("/routines/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_routine_invalid_exercise_id_rejected_without_orphan(client):
    before = await client.get("/routines")
    before_names = {r["name"] for r in before.json()}

    resp = await client.post(
        "/routines", json={"name": "Bogus Routine", "exercise_ids": [999999]}
    )
    assert 400 <= resp.status_code < 500

    after = await client.get("/routines")
    after_names = {r["name"] for r in after.json()}
    assert after_names == before_names
    assert "Bogus Routine" not in after_names
