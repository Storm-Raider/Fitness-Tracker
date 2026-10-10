"""TODO-CC-5: the coach must see a workout you just finished.

build_profile() caches per user for 30 minutes. Plan generation read that
cache, so a workout finished right before pressing Generate was ignored.
Generation now builds a fresh profile (cheap next to the model call), and
finishing a workout drops the cached one so the Plan page is current too.
"""
import pytest

from app.routes import coach
from app.utils import training_profile


@pytest.fixture(autouse=True)
def _clean_state():
    training_profile._PROFILE_CACHE.clear()
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear(); coach._LAST_BY_USER.clear()
    yield
    training_profile._PROFILE_CACHE.clear()
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear(); coach._LAST_BY_USER.clear()


async def _finish_a_workout(client, db):
    async with db.execute("SELECT id FROM exercises WHERE name = 'Bench Press'") as cur:
        ex = (await cur.fetchone())["id"]
    w = (await client.post("/workouts", json={})).json()["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 80})
    assert (await client.post(f"/workouts/{w}/finish")).status_code == 200


async def _plan_profile(client):
    resp = await client.get("/plan")            # JSON (no Accept: text/html)
    assert resp.status_code == 200
    return resp.json()["profile"]


@pytest.mark.asyncio
async def test_generation_sees_a_workout_finished_after_the_cache_was_filled(client, db, monkeypatch):
    assert (await _plan_profile(client))["total_workouts"] == 0     # cache now holds the empty profile

    seen = {}
    real_generate = coach._generate_plan

    async def spy(conn, profile, *args, **kwargs):
        seen["total_workouts"] = profile["total_workouts"]
        return await real_generate(conn, profile, *args, **kwargs)

    async def fake_chat(system, user, schema, **kwargs):
        return {"title": "T", "summary": "", "days": [{"focus": "A", "exercises": [{"name": "Bench Press", "sets": 3, "reps": "5"}]}]}

    monkeypatch.setattr(coach, "_generate_plan", spy)
    monkeypatch.setattr(coach.gemini, "chat_json", fake_chat)
    monkeypatch.setattr(coach.gemini, "is_configured", lambda: True)

    await _finish_a_workout(client, db)
    from tests.test_coach import _generate
    assert (await _generate(client, "strength", 1))["status"] == "done"
    assert seen["total_workouts"] == 1


@pytest.mark.asyncio
async def test_finishing_a_workout_refreshes_the_plan_page_profile(client, db):
    assert (await _plan_profile(client))["total_workouts"] == 0
    await _finish_a_workout(client, db)
    assert (await _plan_profile(client))["total_workouts"] == 1
