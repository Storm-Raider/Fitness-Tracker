import pytest


@pytest.mark.asyncio
async def test_prs_page_renders_empty(client):
    resp = await client.get("/prs", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Personal Records" in resp.text
    assert "No sets logged yet" in resp.text


@pytest.mark.asyncio
async def test_prs_page_shows_record(client):
    ex = await client.post("/exercises", json={"name": "PR Test Lift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 120.0})

    resp = await client.get("/prs", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "PR Test Lift" in resp.text
    assert "120.0" in resp.text
