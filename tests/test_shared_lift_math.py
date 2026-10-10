"""One definition of the lift math shared by the analytics page and the coach.

The "stalled lifts" query was copied into app/routes/analytics.py and
app/utils/training_profile.py and the copies drifted: the coach excluded
bodyweight exercises (their weight_kg is the lifter's bodyweight, so a flat
e1RM is meaningless) and the analytics page did not. The Epley estimate was
written out in nine SQL queries.
"""
from datetime import datetime, timedelta

import pytest

from app.utils import training_profile
from app.utils.training_profile import build_profile


@pytest.fixture(autouse=True)
def _fresh_profile():
    """build_profile caches per uid for 30 min (TODO-CC-5); another test's uid 1 must not leak in."""
    training_profile._PROFILE_CACHE.clear()
    yield
    training_profile._PROFILE_CACHE.clear()


async def _exercise_id(db, name):
    async with db.execute("SELECT id FROM exercises WHERE name = ?", (name,)) as cur:
        return (await cur.fetchone())["id"]


async def _flat_history(db, exercise_id, weight_kg, first_workout_id):
    """Four finished sessions, two 28–84 days ago and two in the last 28, all
    the same load: a flat e1RM, which is what "stalled" means."""
    for i, days_ago in enumerate((80, 50, 20, 5)):
        start = datetime.now() - timedelta(days=days_ago)
        wid = first_workout_id + i
        await db.execute(
            "INSERT INTO workouts(id, started_at, ended_at, user_id) VALUES (?, ?, ?, 1)",
            (wid, start.strftime("%Y-%m-%d %H:%M:%S"),
             (start + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")),
        )
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?, ?, 5, ?, 1)",
            (wid, exercise_id, weight_kg),
        )
    await db.commit()


@pytest.mark.asyncio
async def test_analytics_and_coach_agree_on_stalled_lifts(client, db):
    await _flat_history(db, await _exercise_id(db, "Bench Press"), 80.0, 1)
    await _flat_history(db, await _exercise_id(db, "Crunch"), 61.1, 11)

    page = (await client.get("/analytics")).json()
    on_page = {s["name"] for s in page["stalled"]}
    for_coach = set((await build_profile(db, uid=1))["stalled"])

    assert "Bench Press" in on_page and "Bench Press" in for_coach   # positive control
    assert "Crunch" not in on_page                                    # bodyweight: excluded on both
    assert "Crunch" not in for_coach


@pytest.mark.asyncio
async def test_e1rm_is_one_sql_function(db):
    async with db.execute("SELECT e1rm(100, 5) AS v, e1rm(100, 0) AS zero") as cur:
        row = await cur.fetchone()
    assert row["v"] == pytest.approx(116.667, abs=0.001)
    assert row["zero"] == pytest.approx(100.0)
