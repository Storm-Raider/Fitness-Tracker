"""Home's weekly recap: last Monday-to-Sunday in one line, shown Monday to
Wednesday."""
from datetime import date

import pytest

from app.utils import weekly_recap

HTML = {"Accept": "text/html"}
MON = date(2026, 10, 12)          # a Monday; last week is Oct 5 to Oct 11


async def _session(db, day: str, sets: list[tuple[int, float, int]], uid: int = 1):
    """One finished session on `day` with (exercise_id, weight_kg, reps) sets."""
    cur = await db.execute(
        "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (?, ?, ?)",
        (uid, f"{day}T09:00:00", f"{day}T10:00:00"),
    )
    for ex_id, kg, reps in sets:
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, user_id, reps, weight_kg) VALUES (?, ?, ?, ?, ?)",
            (cur.lastrowid, ex_id, uid, reps, kg),
        )
    await db.commit()


async def _exercise(db, name: str) -> int:
    cur = await db.execute("INSERT INTO exercises(name) VALUES (?)", (f"Recap {name}",))
    await db.commit()
    return cur.lastrowid


@pytest.mark.asyncio
async def test_recap_hidden_thursday_to_sunday(db):
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-10-06", [(ex, 100, 5)])
    for d in (15, 16, 17, 18):            # Thu..Sun
        assert await weekly_recap.last_week_recap(db, 1, date(2026, 10, d)) is None
    for d in (12, 13, 14):                # Mon..Wed
        assert await weekly_recap.last_week_recap(db, 1, date(2026, 10, d)) is not None


@pytest.mark.asyncio
async def test_recap_hidden_for_a_new_user(db):
    assert await weekly_recap.last_week_recap(db, 1, MON) is None


@pytest.mark.asyncio
async def test_recap_counts_last_calendar_week_only(db):
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-09-29", [(ex, 80, 5)])                 # the week before: 400 kg
    await _session(db, "2026-10-05", [(ex, 100, 5)])                # last Monday: 500
    await _session(db, "2026-10-11", [(ex, 100, 5), (ex, 100, 5)])  # last Sunday: 1000
    await _session(db, "2026-10-12", [(ex, 200, 5)])                # today: not counted
    await db.execute(
        "INSERT INTO cardio_logs(user_id, logged_date, duration_minutes) VALUES (1, '2026-10-08', 30)"
    )
    await db.commit()

    r = await weekly_recap.last_week_recap(db, 1, MON)
    assert r["week_start"] == "2026-10-05" and r["week_end"] == "2026-10-11"
    assert r["label"] == "Oct 5 – 11"
    assert r["sessions"] == 3                 # two lifting days and a run
    assert r["volume_kg"] == 1500
    assert r["volume_delta_pct"] == 275       # 1500 vs 400
    # Today's 200 kg set is the all-time record now, so last week holds none.
    assert r["records"] == 0


@pytest.mark.asyncio
async def test_recap_records_and_no_prior_week(db):
    sq = await _exercise(db, "Squat")
    bp = await _exercise(db, "Bench")
    await _session(db, "2026-10-07", [(sq, 120, 3), (bp, 80, 5)])
    r = await weekly_recap.last_week_recap(db, 1, MON)
    assert r["records"] == 2
    assert r["volume_delta_pct"] is None      # nothing to compare against


@pytest.mark.asyncio
async def test_recap_quiet_week(db):
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-09-22", [(ex, 100, 5)])
    r = await weekly_recap.last_week_recap(db, 1, MON)
    assert r["sessions"] == 0 and r["volume_kg"] == 0


@pytest.mark.asyncio
async def test_recap_readiness_from_oura(db):
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-10-06", [(ex, 100, 5)])
    r = await weekly_recap.last_week_recap(db, 1, MON)
    assert r["readiness"] is None
    for day, score in (("2026-10-05", 70), ("2026-10-08", 80), ("2026-10-12", 20)):
        await db.execute(
            "INSERT INTO oura_daily(user_id, day, readiness_score) VALUES (1, ?, ?)", (day, score)
        )
    await db.commit()
    r = await weekly_recap.last_week_recap(db, 1, MON)
    assert r["readiness"] == 75
    assert r["readiness_band"] == "Good"


@pytest.mark.asyncio
async def test_recap_is_per_user(db, user_b):
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-10-06", [(ex, 100, 5)], uid=user_b["id"])
    assert await weekly_recap.last_week_recap(db, 1, MON) is None


@pytest.mark.asyncio
async def test_home_shows_recap_on_monday(client, db, monkeypatch):
    monkeypatch.setattr(weekly_recap, "_today", lambda: MON)
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-10-06", [(ex, 100, 5)])
    await _session(db, "2026-10-08", [(ex, 100, 5)])
    await db.execute("INSERT OR REPLACE INTO user_settings(user_id, weekly_goal_sessions) VALUES (1, 3)")
    await db.commit()
    html = (await client.get("/", headers=HTML)).text
    assert 'class="recap"' in html
    assert "Last week" in html
    assert "Oct 5 – 11" in html
    assert '<b class="num">2</b> of <span class="num">3</span> sessions' in html


@pytest.mark.asyncio
async def test_home_has_no_recap_on_thursday(client, db, monkeypatch):
    monkeypatch.setattr(weekly_recap, "_today", lambda: date(2026, 10, 15))
    ex = await _exercise(db, "Squat")
    await _session(db, "2026-10-13", [(ex, 100, 5)])
    html = (await client.get("/", headers=HTML)).text
    assert 'class="recap"' not in html


def test_range_label_across_months():
    assert weekly_recap._range_label(date(2026, 9, 28), date(2026, 10, 4)) == "Sep 28 – Oct 4"
