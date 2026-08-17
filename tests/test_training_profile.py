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


# ── Comments/history the coach previously never read: set notes, RPE, journal ──

async def _log_set_full(
    db, workout_id: int, exercise_id: int, reps: int, weight_kg: float,
    notes: str | None = None, rpe: int | None = None,
) -> None:
    await db.execute(
        "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, rpe, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (workout_id, exercise_id, reps, weight_kg, notes, rpe),
    )


@pytest.mark.asyncio
async def test_profile_surfaces_recent_set_notes(db):
    await db.execute(
        "INSERT INTO workouts(id, started_at, ended_at, user_id) "
        "VALUES (1, datetime('now','localtime','-1 day'), datetime('now','localtime','-1 day','+30 minutes'), 1)"
    )
    bench_id = await _exercise_id(db, "Bench Press")
    await _log_set_full(db, 1, bench_id, reps=5, weight_kg=100.0, notes="left shoulder felt off on the last rep")
    await db.commit()

    profile = await build_profile(db, uid=1)

    assert profile["recent_set_notes"], "recent set notes were not picked up"
    note = profile["recent_set_notes"][0]
    assert note["name"] == "Bench Press"
    assert "shoulder" in note["notes"]


@pytest.mark.asyncio
async def test_profile_flags_pain_from_set_notes(db):
    await db.execute(
        "INSERT INTO workouts(id, started_at, ended_at, user_id) "
        "VALUES (1, datetime('now','localtime','-1 day'), datetime('now','localtime','-1 day','+30 minutes'), 1)"
    )
    squat_id = await _exercise_id(db, "Back Squat")
    await _log_set_full(db, 1, squat_id, reps=5, weight_kg=100.0, notes="tweaked my knee on the descent")
    await db.commit()

    profile = await build_profile(db, uid=1)

    assert profile["injury_flags"], "pain keyword in a set note should raise an injury flag"
    flag = profile["injury_flags"][0]
    assert flag["exercise"] == "Back Squat"
    assert "knee" in flag["text"]


@pytest.mark.asyncio
async def test_profile_does_not_flag_generic_soreness(db):
    """Plain 'sore' is normal DOMS language, not an injury — must not false-positive."""
    await db.execute(
        "INSERT INTO workouts(id, started_at, ended_at, user_id) "
        "VALUES (1, datetime('now','localtime','-1 day'), datetime('now','localtime','-1 day','+30 minutes'), 1)"
    )
    squat_id = await _exercise_id(db, "Back Squat")
    await _log_set_full(db, 1, squat_id, reps=5, weight_kg=100.0, notes="quads a bit sore today, felt fine")
    await db.commit()

    profile = await build_profile(db, uid=1)

    assert profile["injury_flags"] == []


@pytest.mark.asyncio
async def test_profile_computes_rpe_trend(db):
    await db.execute(
        "INSERT INTO workouts(id, started_at, ended_at, user_id) "
        "VALUES (1, datetime('now','localtime','-1 day'), datetime('now','localtime','-1 day','+30 minutes'), 1)"
    )
    squat_id = await _exercise_id(db, "Back Squat")
    curl_id = await _exercise_id(db, "Barbell Curl")
    # Squat: consistently near-max effort → HIGH.
    await _log_set_full(db, 1, squat_id, reps=3, weight_kg=140.0, rpe=9)
    await _log_set_full(db, 1, squat_id, reps=3, weight_kg=140.0, rpe=9)
    # Curl: consistently easy → LOW.
    await _log_set_full(db, 1, curl_id, reps=10, weight_kg=20.0, rpe=4)
    await _log_set_full(db, 1, curl_id, reps=10, weight_kg=20.0, rpe=5)
    await db.commit()

    profile = await build_profile(db, uid=1)

    high_names = {l["name"] for l in profile["high_effort_lifts"]}
    low_names = {l["name"] for l in profile["low_effort_lifts"]}
    assert "Back Squat" in high_names
    assert "Barbell Curl" in low_names


@pytest.mark.asyncio
async def test_profile_reads_journal_wellness_and_flags_pain(db):
    from datetime import date

    today = date.today().isoformat()
    await db.execute(
        "INSERT INTO daily_logs(user_id, log_date, sleep_hrs, energy, motivation, notes) "
        "VALUES (1, ?, 5.0, 'low', 'low', 'lower back pain after yesterday, taking it easy')",
        (today,),
    )
    await db.commit()

    profile = await build_profile(db, uid=1)

    assert profile["wellness"]["avg_sleep_hrs"] == 5.0
    assert profile["wellness"]["low_energy_days"] == 1
    assert profile["wellness"]["low_motivation_days"] == 1
    assert profile["wellness"]["recent_notes"]
    assert any("back pain" in f["text"] for f in profile["injury_flags"])
