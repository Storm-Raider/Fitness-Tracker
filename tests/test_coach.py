import asyncio
import json

import pytest

from app.routes import coach


async def _generate(client, goal, days, **extra):
    """Start a generation job and poll the status endpoint until it settles."""
    r = await client.post(
        "/coach/generate", json={"goal": goal, "days_per_week": days, **extra}
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    for _ in range(100):
        pr = await client.get(f"/coach/generate/{job_id}")
        assert pr.status_code == 200
        pd = pr.json()
        if pd["status"] != "pending":
            return pd
        await asyncio.sleep(0.02)
    raise AssertionError("generation job never finished")


async def _real_exercise_names(db, n=3):
    """Pull real seeded exercise names so fake plans resolve to valid ids."""
    async with db.execute(
        "SELECT name FROM exercises WHERE COALESCE(category,'') != 'Cardio' ORDER BY name LIMIT ?",
        (n,),
    ) as cur:
        return [r["name"] for r in await cur.fetchall()]


def _fake_chat(plan: dict):
    async def _inner(system, user, schema, **kwargs):
        return plan
    return _inner


@pytest.mark.asyncio
async def test_coach_page_renders(client, monkeypatch):
    async def fake_avail(timeout=3.0):
        return False, []
    monkeypatch.setattr(coach.ollama, "is_available", fake_avail)

    resp = await client.get("/coach")
    assert resp.status_code == 200
    assert "AI Coach" in resp.text
    # Ollama-down notice should surface
    assert "isn't reachable" in resp.text


@pytest.mark.asyncio
async def test_coach_page_requires_auth(anon_client):
    resp = await anon_client.get("/coach", follow_redirects=False)
    assert resp.status_code in (302, 401)


@pytest.mark.asyncio
async def test_generate_returns_plan_and_drops_unknowns(client, db, monkeypatch):
    names = await _real_exercise_names(db, 3)
    fake_plan = {
        "title": "Test Split",
        "summary": "A test routine.",
        "days": [
            {"focus": "Day A", "exercises": [
                {"name": names[0], "sets": 4, "reps": "8-12", "note": "controlled"},
                {"name": "Totally Fake Lift", "sets": 3, "reps": "10"},
            ]},
            {"focus": "Day B", "exercises": [
                {"name": names[1], "sets": 5, "reps": "5"},
                {"name": names[2], "sets": 3, "reps": "12"},
            ]},
        ],
    }
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(fake_plan))

    data = await _generate(client, "strength", 2)
    assert data["status"] == "done"
    assert data["plan"]["goal"] == "strength"
    assert len(data["plan"]["days"]) == 2
    # Unknown exercise filtered out, real ones resolved with ids
    day_a = data["plan"]["days"][0]
    assert [e["name"] for e in day_a["exercises"]] == [names[0]]
    assert day_a["exercises"][0]["exercise_id"] > 0
    assert "Totally Fake Lift" in data["dropped"]


@pytest.mark.asyncio
async def test_generate_caps_days_to_request(client, db, monkeypatch):
    names = await _real_exercise_names(db, 2)
    fake_plan = {
        "title": "Big Plan", "summary": "",
        "days": [
            {"focus": f"D{i}", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}
            for i in range(5)
        ],
    }
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(fake_plan))

    data = await _generate(client, "general", 3)
    assert data["status"] == "done"
    assert len(data["plan"]["days"]) == 3


@pytest.mark.asyncio
async def test_generate_handles_ollama_error(client, monkeypatch):
    async def boom(system, user, schema, **kwargs):
        raise coach.ollama.OllamaError("Couldn't reach Ollama")
    monkeypatch.setattr(coach.ollama, "chat_json", boom)

    data = await _generate(client, "strength", 3)
    assert data["status"] == "error"
    assert "Ollama" in data["error"]


@pytest.mark.asyncio
async def test_generate_empty_plan_errors(client, monkeypatch):
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat({"title": "x", "summary": "", "days": []}))
    data = await _generate(client, "strength", 3)
    assert data["status"] == "error"
    assert "usable exercises" in data["error"]


@pytest.mark.asyncio
async def test_generate_validates_input(client):
    resp = await client.post("/coach/generate", json={"goal": "bogus", "days_per_week": 3})
    assert resp.status_code == 422
    resp = await client.post("/coach/generate", json={"goal": "strength", "days_per_week": 99})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_generation_status_unknown_job_404(client):
    resp = await client.get("/coach/generate/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_single_flight_reuses_inflight_job(client, db, monkeypatch):
    # A second request while one is still running must reuse the same job_id,
    # so repeated clicks / a stale tab can't spawn multiple Ollama generations.
    names = await _real_exercise_names(db, 1)
    gate = asyncio.Event()

    async def slow(system, user, schema, **kwargs):
        await gate.wait()
        return {"title": "P", "summary": "", "days": [
            {"focus": "A", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}]}
    monkeypatch.setattr(coach.ollama, "chat_json", slow)

    r1 = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 1})
    r2 = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 1})
    assert r1.json()["job_id"] == r2.json()["job_id"]

    gate.set()  # let the single job finish
    job_id = r1.json()["job_id"]
    for _ in range(100):
        pd = (await client.get(f"/coach/generate/{job_id}")).json()
        if pd["status"] != "pending":
            break
        await asyncio.sleep(0.02)
    assert pd["status"] == "done"


@pytest.mark.asyncio
async def test_generation_job_isolated_between_users(client, user_b_client, db, monkeypatch):
    names = await _real_exercise_names(db, 1)
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(
        {"title": "P", "summary": "", "days": [
            {"focus": "A", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}]}
    ))
    r = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 1})
    job_id = r.json()["job_id"]
    # user B cannot read user A's job
    resp = await user_b_client.get(f"/coach/generate/{job_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_save_creates_plan_and_routines(client, db):
    names = await _real_exercise_names(db, 2)
    payload = {
        "title": "My Coached Plan",
        "summary": "summary",
        "goal": "hypertrophy",
        "days_per_week": 2,
        "days": [
            {"focus": "Upper", "exercises": [{"name": names[0], "sets": 4, "reps": "8-12", "note": ""}]},
            {"focus": "Lower", "exercises": [{"name": names[1], "sets": 3, "reps": "10", "note": "deep"}]},
        ],
    }
    resp = await client.post("/coach/save", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["routine_ids"]) == 2

    # coach_plan persisted
    async with db.execute("SELECT title, plan_json FROM coach_plans WHERE id = ?", (data["id"],)) as cur:
        row = await cur.fetchone()
    assert row["title"] == "My Coached Plan"
    plan = json.loads(row["plan_json"])
    assert plan["days"][0]["exercises"][0]["exercise_id"] > 0

    # routines show up in the user's routine list with day labels
    listing = await client.get("/routines")
    routine_names = [r["name"] for r in listing.json()]
    assert any("My Coached Plan · Day 1: Upper" == n for n in routine_names)
    assert any("Day 2: Lower" in n for n in routine_names)


@pytest.mark.asyncio
async def test_save_rejects_all_invalid_exercises(client):
    payload = {
        "title": "Bad Plan", "summary": "", "goal": "strength", "days_per_week": 1,
        "days": [{"focus": "X", "exercises": [{"name": "Nonexistent Move", "sets": 3, "reps": "5"}]}],
    }
    resp = await client.post("/coach/save", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_plan(client, db):
    names = await _real_exercise_names(db, 1)
    payload = {
        "title": "Deletable", "summary": "", "goal": "general", "days_per_week": 1,
        "days": [{"focus": "Full", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}],
    }
    pid = (await client.post("/coach/save", json=payload)).json()["id"]

    resp = await client.delete(f"/coach/plans/{pid}")
    assert resp.status_code == 204

    resp = await client.delete(f"/coach/plans/{pid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_plan_isolation_between_users(client, user_b_client, db):
    names = await _real_exercise_names(db, 1)
    payload = {
        "title": "Private", "summary": "", "goal": "general", "days_per_week": 1,
        "days": [{"focus": "Full", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}],
    }
    pid = (await client.post("/coach/save", json=payload)).json()["id"]

    # user B cannot delete user A's plan
    resp = await user_b_client.delete(f"/coach/plans/{pid}")
    assert resp.status_code == 404
