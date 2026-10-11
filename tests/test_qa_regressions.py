"""Regressions found by the /qa pass on 2026-10-11 (report: .gstack/qa-reports/).
Each test reproduced its bug on main before the fix."""
import json
from datetime import date, timedelta

import pytest


async def _cardio_ex(db):
    async with db.execute("SELECT id FROM exercises WHERE category='Cardio' LIMIT 1") as c:
        return (await c.fetchone())["id"]


# Regression: ISSUE-001 — Sessions page 500s on an old unfinished cardio-only session
@pytest.mark.asyncio
async def test_sessions_page_survives_an_old_cardio_only_session(client, db):
    """The ghost-session cleanup deleted unfinished workouts with no sets, but
    cardio_logs still pointed at them (FK, no cascade): IntegrityError, 500 on
    every visit until the session was finished."""
    cex = await _cardio_ex(db)
    w = (await client.post("/workouts", json={})).json()["id"]
    assert (await client.post(f"/workouts/{w}/cardio", json={"exercise_id": cex, "duration_minutes": 30})).status_code == 201
    await db.execute("UPDATE workouts SET started_at = datetime('now','localtime','-2 hours') WHERE id=?", (w,))
    await db.commit()
    r = await client.get("/workouts", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert (await client.get(f"/workouts/{w}")).status_code == 200      # the session and its cardio survive


RULES_HARD = ["workout1", "workout2", "diet", "water", "read", "photo"]


async def _attempt(db, key, title, total, start):
    async with db.execute(
        "INSERT INTO challenge_attempts(user_id,template_key,title,total_days,status,started_on,created_at) "
        "VALUES (1,?,?,?,'active',?,?)", (key, title, total, start.isoformat(), start.isoformat() + " 08:00:00")) as c:
        aid = c.lastrowid
    await db.commit()
    return aid


async def _tick(db, aid, start, end, keys=RULES_HARD):
    d = start
    while d <= end:
        await db.execute("INSERT INTO challenge_checkins(attempt_id,user_id,day_date,rules_json) VALUES (?,1,?,?)",
                         (aid, d.isoformat(), json.dumps({k: True for k in keys})))
        d += timedelta(days=1)
    await db.commit()


async def _status(db, aid):
    async with db.execute("SELECT status, ended_on FROM challenge_attempts WHERE id=?", (aid,)) as c:
        return tuple(await c.fetchone())


# Regression: ISSUE-002 — the last day of a challenge took away yesterday's grace
@pytest.mark.asyncio
async def test_last_day_keeps_yesterdays_grace(client, db):
    """Day 75 of 75 Hard, days 1-73 done, 74 and 75 not ticked yet: opening the
    page failed the run, though the page itself offers day 74 as editable."""
    today = date.today()
    start = today - timedelta(days=74)
    aid = await _attempt(db, "75_hard", "75 Hard", 75, start)
    await _tick(db, aid, start, today - timedelta(days=2))
    assert (await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})).status_code == 200
    assert (await _status(db, aid))[0] == "active"
    # ticking 74 and 75 now completes it
    await _tick(db, aid, today - timedelta(days=1), today)
    await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert await _status(db, aid) == ("completed", today.isoformat())


# Regression: ISSUE-003 — a missed final day left the challenge active forever
@pytest.mark.asyncio
async def test_missed_final_day_ends_the_run(client, db):
    today = date.today()
    start = today - timedelta(days=80)                  # last day was 6 days ago
    last_day = start + timedelta(days=74)
    aid = await _attempt(db, "75_hard", "75 Hard", 75, start)
    await _tick(db, aid, start, last_day - timedelta(days=1))
    await client.get("/", headers={"Accept": "text/html"})
    assert await _status(db, aid) == ("failed", last_day.isoformat())


@pytest.mark.asyncio
async def test_no_fail_run_left_incomplete_ends_after_its_grace(client, db):
    today = date.today()
    start = today - timedelta(days=80)
    aid = await _attempt(db, "75_medium", "75 Medium", 75, start)   # no ticks at all
    await client.get("/", headers={"Accept": "text/html"})
    assert (await _status(db, aid))[0] == "failed"


@pytest.mark.asyncio
async def test_backdated_start_beyond_the_length_is_rejected(client):
    too_early = (date.today() - timedelta(days=100)).isoformat()
    assert (await client.post("/challenges", json={"template_key": "75_hard", "start_date": too_early})).status_code == 422
    ok = (date.today() - timedelta(days=74)).isoformat()             # today is day 75: still inside
    assert (await client.post("/challenges", json={"template_key": "75_hard", "start_date": ok})).status_code == 201


# Regression: ISSUE-005 — a future-dated activity reset the current streak to 0
def test_streak_ignores_future_dates():
    from app.utils.streak import compute_streak
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(3)]
    assert compute_streak(days) == 3
    assert compute_streak(days + [(today + timedelta(days=1)).isoformat()]) == 3
    assert compute_streak([(today + timedelta(days=2)).isoformat()]) == 0


@pytest.mark.asyncio
async def test_cardio_cannot_be_logged_in_the_future(client, db):
    cid = await _cardio_ex(db)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = await client.post("/cardio", data={"exercise_id": cid, "duration_minutes": 30, "logged_date": tomorrow},
                          follow_redirects=False)
    assert r.status_code == 422
    r = await client.post("/cardio", data={"exercise_id": cid, "duration_minutes": 30,
                                           "logged_date": date.today().isoformat()}, follow_redirects=False)
    assert r.status_code == 303


# Regression: ISSUE-006 — a username differing only in case locked one account out
@pytest.mark.asyncio
async def test_invite_rejects_a_username_that_differs_only_in_case(anon_client, db):
    await db.execute("INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
                     "VALUES ('qa-tok', 1, datetime('now','localtime','+1 day'), 5)")
    await db.commit()
    async with db.execute("SELECT username FROM users WHERE id = 1") as c:
        existing = (await c.fetchone())["username"]
    r = await anon_client.post("/invite/accept/qa-tok", data={
        "username": existing.swapcase(), "email": "qa-case@example.com",
        "password": "another-pass-123", "password_confirm": "another-pass-123"}, follow_redirects=False)
    assert r.status_code == 200 and "Username is already taken" in r.text
    async with db.execute("SELECT COUNT(*) AS n FROM users WHERE LOWER(username) = LOWER(?)", (existing,)) as c:
        assert (await c.fetchone())["n"] == 1


# Regression: ISSUE-007 — a finished cardio-only session was missing from Sessions
@pytest.mark.asyncio
async def test_finished_cardio_only_session_is_listed(client, db):
    cex = await _cardio_ex(db)
    w = (await client.post("/workouts", json={})).json()["id"]
    await client.post(f"/workouts/{w}/cardio", json={"exercise_id": cex, "duration_minutes": 30})
    assert (await client.post(f"/workouts/{w}/finish")).status_code == 200
    page = (await client.get("/workouts", headers={"Accept": "text/html"})).text
    assert f'href="/workouts/{w}"' in page


# Regression: ISSUE-008 — searching Sessions hid the live session (start without warning)
@pytest.mark.asyncio
async def test_search_keeps_the_live_session_resume_card(client):
    ex = (await client.post("/exercises", json={"name": "Searchable Lift"})).json()["id"]
    done = (await client.post("/workouts", json={})).json()["id"]
    await client.post(f"/workouts/{done}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 50})
    await client.post(f"/workouts/{done}/finish")
    live = (await client.post("/workouts", json={})).json()["id"]          # nothing logged yet
    page = (await client.get("/workouts?q=searchable", headers={"Accept": "text/html"})).text
    assert f'href="/workouts/{done}"' in page
    assert "Resume session" in page and 'onclick="startSession(this)"' not in page
    assert f"_activeWorkoutId = {live}" in page


# Regression: ISSUE-009 — every 0 kg set after the first was a "new record"
@pytest.mark.asyncio
async def test_zero_kg_sets_are_not_a_record_every_time(client):
    ex = (await client.post("/exercises", json={"name": "QA Band Pull Apart"})).json()["id"]
    w = (await client.post("/workouts", json={})).json()["id"]
    first = (await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 15, "weight_kg": 0})).json()
    again = (await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 15, "weight_kg": 0})).json()
    heavier = (await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 10, "weight_kg": 2.5})).json()
    assert (first["is_pr"], again["is_pr"], heavier["is_pr"]) == (True, False, True)


# Regression: ISSUE-010 — editing a hold or bodyweight set in the finished view corrupted it
@pytest.mark.asyncio
async def test_patch_keeps_a_holds_and_a_bodyweight_sets_numbers(client, db):
    async with db.execute("SELECT id FROM exercises WHERE name='Plank'") as c:
        plank = (await c.fetchone())["id"]
    async with db.execute("SELECT id FROM exercises WHERE name='Pull-up'") as c:
        pull = (await c.fetchone())["id"]
    w = (await client.post("/workouts", json={})).json()["id"]
    hold = (await client.post(f"/workouts/{w}/sets", json={"exercise_id": plank, "reps": 1, "weight_kg": 80,
                                                            "added_weight_kg": 0, "duration_seconds": 60})).json()["id"]
    bw = (await client.post(f"/workouts/{w}/sets", json={"exercise_id": pull, "reps": 8, "weight_kg": 85,
                                                          "added_weight_kg": 5})).json()["id"]
    before = (await client.post(f"/workouts/{w}/finish")).json()["volume_kg"]
    r = await client.patch(f"/workouts/{w}/sets/{hold}", json={"reps": 1, "weight_kg": 80, "notes": "felt good", "rpe": 7})
    assert r.status_code == 200 and r.json()["reps"] == 0
    await client.patch(f"/workouts/{w}/sets/{bw}", json={"reps": 3, "weight_kg": 5, "notes": None, "rpe": 9})
    async with db.execute("SELECT id, reps, weight_kg, notes, rpe FROM sets WHERE workout_id=? ORDER BY id", (w,)) as c:
        rows = [tuple(r) for r in await c.fetchall()]
    assert rows == [(hold, 0, 80.0, "felt good", 7), (bw, 8, 85.0, None, 9)]
    assert (await client.post(f"/workouts/{w}/finish")).json()["volume_kg"] == before


# Regression: ISSUE-011 — undo restored rows with new ids (wrong order, wrong "Last time")
@pytest.mark.asyncio
async def test_undo_puts_a_set_back_in_its_place(client, db):
    a_ex = (await client.post("/exercises", json={"name": "QA Order Lift"})).json()["id"]
    b_ex = (await client.post("/exercises", json={"name": "QA Other Lift"})).json()["id"]
    w = (await client.post("/workouts", json={})).json()["id"]
    first = (await client.post(f"/workouts/{w}/sets", json={"exercise_id": a_ex, "reps": 5, "weight_kg": 100})).json()["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": a_ex, "reps": 5, "weight_kg": 110})
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": b_ex, "reps": 5, "weight_kg": 50})
    tok = (await client.delete(f"/workouts/{w}/sets/{first}")).headers["X-Undo-Token"]
    assert (await client.post(f"/undo/{tok}")).status_code == 200
    async with db.execute("SELECT id, weight_kg FROM sets WHERE workout_id=? ORDER BY id", (w,)) as c:
        rows = [tuple(r) for r in await c.fetchall()]
    assert rows[0] == (first, 100.0) and [r[1] for r in rows] == [100.0, 110.0, 50.0]


@pytest.mark.asyncio
async def test_undoing_an_old_session_keeps_last_time_on_the_newest(client):
    ex = (await client.post("/exercises", json={"name": "QA Hint Lift"})).json()["id"]
    old = (await client.post("/workouts", json={})).json()["id"]
    await client.post(f"/workouts/{old}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 60})
    await client.post(f"/workouts/{old}/finish")
    new = (await client.post("/workouts", json={})).json()["id"]
    await client.post(f"/workouts/{new}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 100})
    await client.post(f"/workouts/{new}/finish")
    tok = (await client.delete(f"/workouts/{old}")).headers["X-Undo-Token"]
    assert (await client.post(f"/undo/{tok}")).status_code == 200
    assert (await client.get(f"/workouts/{old}")).status_code == 200          # same URL as before
    cur = (await client.post("/workouts", json={})).json()["id"]
    last = (await client.get(f"/api/exercises?exclude_workout={cur}")).json()["last_sets"][str(ex)]
    assert last["weight_kg"] == 100


# Regression: ISSUE-012 — the week spanning New Year lost its new-year days in Analytics
@pytest.mark.asyncio
async def test_analytics_week_across_new_year_keeps_all_its_days(client, db, monkeypatch):
    import re
    import app.routes.analytics as an

    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2027, 1, 4)              # the Monday after New Year

    monkeypatch.setattr(an, "_date", FakeDate)
    ex = (await client.post("/exercises", json={"name": "QA Year Lift"})).json()["id"]
    for day, kg in (("2026-12-30", 50), ("2027-01-02", 100)):   # Wed + Sat of one week
        async with db.execute("INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
                              (f"{day} 10:00:00", f"{day} 11:00:00")) as c:
            wid = c.lastrowid
        await db.execute("INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,10,?,1)",
                         (wid, ex, kg))
    await db.commit()
    page = (await client.get("/analytics", headers={"Accept": "text/html"})).text
    weeks = json.loads(re.search(r"const DATA\s*=\s*(\[.*?\]);", page, re.S).group(1))
    by_start = {w["week_start"]: w["volume"] for w in weeks}
    assert by_start["2026-12-28"] == 1500.0 and weeks[-1]["week_start"] == "2027-01-04"
