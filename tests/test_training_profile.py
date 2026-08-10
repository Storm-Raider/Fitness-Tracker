from datetime import datetime, timedelta

import pytest

from app.utils import training_profile
from app.utils.training_profile import build_profile


@pytest.fixture(autouse=True)
def _reset_profile_cache():
    """build_profile() caches per-uid in a process-global dict — clear it
    around every test so one test's cached profile can't leak into the next."""
    training_profile._PROFILE_CACHE.clear()
    yield
    training_profile._PROFILE_CACHE.clear()


async def _exercise_id(db, name: str) -> int:
    async with db.execute("SELECT id FROM exercises WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
        assert row is not None, f"seeded exercise {name!r} not found"
        return row["id"]


async def _log_set(db, workout_id: int, exercise_id: int, reps: int, weight_kg: float) -> None:
    await db.execute(
        "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) "
        "VALUES (?, ?, ?, ?, 1)",
        (workout_id, exercise_id, reps, weight_kg),
    )


@pytest.mark.asyncio
async def test_top_lifts_excludes_bodyweight_equipment(db):
    await db.execute(
        "INSERT INTO workouts(id, started_at, ended_at, user_id) "
        "VALUES (1, '2026-08-01 10:00:00', '2026-08-01 10:30:00', 1)"
    )
    crunch_id = await _exercise_id(db, "Crunch")
    bench_id = await _exercise_id(db, "Bench Press")
    # Crunch (Bodyweight equipment) logged with the effective-load convention
    # (weight_kg = bodyweight, no added weight) — must NOT produce an e1RM.
    await _log_set(db, 1, crunch_id, reps=15, weight_kg=61.1)
    # Bench Press (Barbell equipment) — a genuine loaded lift, must still
    # produce an e1RM as before.
    await _log_set(db, 1, bench_id, reps=5, weight_kg=100.0)
    await db.commit()

    profile = await build_profile(db, uid=1)
    names = {lift["name"] for lift in profile["top_lifts"]}

    assert "Crunch" not in names
    assert "Bench Press" in names


@pytest.mark.asyncio
async def test_stalled_excludes_bodyweight_equipment(db):
    crunch_id = await _exercise_id(db, "Crunch")

    def _at(days_ago: int) -> tuple[str, str]:
        start = datetime.now() - timedelta(days=days_ago)
        end = start + timedelta(minutes=30)
        return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")

    # 4 sessions: two in the 28-84-day-ago "prior" window, two in the last
    # 28 days ("recent"), all logged with the same reps/weight so recent_1rm
    # == prior_1rm (a flat, "stalled" e1RM trend). This would qualify as
    # "stalled" under the old query, and must be excluded once Bodyweight
    # equipment is filtered out.
    sessions = [_at(80), _at(50), _at(20), _at(5)]
    for i, (started, ended) in enumerate(sessions, start=1):
        await db.execute(
            "INSERT INTO workouts(id, started_at, ended_at, user_id) VALUES (?, ?, ?, 1)",
            (i, started, ended),
        )
        await _log_set(db, i, crunch_id, reps=15, weight_kg=61.1)
    await db.commit()

    profile = await build_profile(db, uid=1)

    assert "Crunch" not in profile["stalled"]
