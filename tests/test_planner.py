import pytest

from app.utils import ollama


async def _fake_avail(timeout=3.0):
    return False, []


@pytest.mark.asyncio
async def test_planner_page_redirects_to_plan(client):
    """GET /planner redirects to /plan with 301."""
    resp = await client.get("/planner", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/plan"


@pytest.mark.asyncio
async def test_plan_page_renders(client, monkeypatch):
    """GET /plan returns 200 with 'Training Plan' in the HTML."""
    monkeypatch.setattr(ollama, "is_available", _fake_avail)

    resp = await client.get("/plan", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Training Plan" in resp.text


@pytest.mark.asyncio
async def test_plan_page_requires_auth(anon_client):
    """Unauthenticated GET /plan redirects to login."""
    resp = await anon_client.get("/plan", follow_redirects=False)
    assert resp.status_code in (302, 401)


@pytest.mark.asyncio
async def test_save_mesocycle_plan(client, db):
    """POST /planner/plans with valid data creates a plan."""
    payload = {
        "name": "My Test Meso",
        "goal": "strength",
        "weeks": 8,
        "lifts": [{"name": "Back Squat", "e1rm_kg": 100.0}],
    }
    resp = await client.post("/planner/plans", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data

    async with db.execute(
        "SELECT name, goal, weeks FROM mesocycle_plans WHERE id=?", (data["id"],)
    ) as cur:
        row = await cur.fetchone()
    assert row["name"] == "My Test Meso"
    assert row["goal"] == "strength"
    assert row["weeks"] == 8


@pytest.mark.asyncio
async def test_save_mesocycle_invalid_goal(client):
    """POST /planner/plans with an invalid goal returns 422."""
    payload = {
        "name": "Bad Goal",
        "goal": "general",  # not a valid mesocycle goal
        "weeks": 8,
        "lifts": [{"name": "Squat", "e1rm_kg": 100.0}],
    }
    resp = await client.post("/planner/plans", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_mesocycle_plan(client, db):
    """DELETE /planner/plans/{id} removes the plan; second delete returns 404."""
    payload = {"name": "Deletable", "goal": "hypertrophy", "weeks": 4, "lifts": []}
    plan_id = (await client.post("/planner/plans", json=payload)).json()["id"]

    resp = await client.delete(f"/planner/plans/{plan_id}")
    assert resp.status_code == 204

    resp = await client.delete(f"/planner/plans/{plan_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_plan_isolation_between_users(client, user_b_client, db):
    """User B cannot delete User A's mesocycle plan."""
    payload = {"name": "Private", "goal": "peaking", "weeks": 6, "lifts": []}
    plan_id = (await client.post("/planner/plans", json=payload)).json()["id"]

    resp = await user_b_client.delete(f"/planner/plans/{plan_id}")
    assert resp.status_code == 404
