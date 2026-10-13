"""Weight suggestions between plans: when the last two sessions of a plan
exercise met its own progression rule, suggest the next step (Plan page Apply /
Not yet, and a chip in the session)."""
import json
from datetime import date, timedelta

import pytest

from app.utils import progression

HTML = {"Accept": "text/html"}


async def _ex(db, name: str) -> dict:
    async with db.execute("SELECT id, name, equipment FROM exercises WHERE name = ?", (name,)) as cur:
        return dict(await cur.fetchone())


async def _session(db, days_ago: int, sets: list[dict], uid: int = 1, finished: bool = True) -> int:
    day = (date.today() - timedelta(days=days_ago)).isoformat()
    cur = await db.execute(
        "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (?, ?, ?)",
        (uid, f"{day}T09:00:00", f"{day}T10:00:00" if finished else None),
    )
    for s in sets:
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, user_id, reps, weight_kg, rpe, notes, added_weight_kg, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cur.lastrowid, s["ex"], uid, s.get("reps", 0), s.get("kg", 0), s.get("rpe"), s.get("notes"),
             s.get("added"), s.get("secs")),
        )
    await db.commit()
    return cur.lastrowid


def _sets(ex_id, kg, reps, n=3, **kw):
    return [{"ex": ex_id, "kg": kg, "reps": reps, **kw} for _ in range(n)]


def _item(ex: dict, sets=3, reps="8-10", load_kg=60.0) -> dict:
    return {"exercise_id": ex["id"], "name": ex["name"], "equipment": ex["equipment"],
            "target": {"sets": sets, "reps": reps, "load_kg": load_kg, "rir": 2}}


async def _ready(db, items, uid=1):
    return await progression.ready(db, uid, items)


# ── The rule ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_sessions_at_the_top_of_the_range_are_ready(db):
    bench = await _ex(db, "Bench Press")
    await _session(db, 5, _sets(bench["id"], 60, 10))
    assert await _ready(db, [_item(bench)]) == {}           # one session is not enough
    await _session(db, 2, [{"ex": bench["id"], "kg": 40, "reps": 10}] + _sets(bench["id"], 60, 10))   # a warm-up is fine
    r = (await _ready(db, [_item(bench)]))[bench["id"]]
    assert r["kind"] == "load" and r["from_kg"] == 60 and r["to_kg"] == 62.5


@pytest.mark.asyncio
async def test_a_missed_rep_a_max_effort_or_pain_blocks_it(db):
    bench = await _ex(db, "Bench Press")
    await _session(db, 5, _sets(bench["id"], 60, 10))
    sid = await _session(db, 2, _sets(bench["id"], 60, 10, n=2) + [{"ex": bench["id"], "kg": 60, "reps": 9}])
    assert await _ready(db, [_item(bench)]) == {}
    await db.execute("UPDATE sets SET reps = 10 WHERE workout_id = ?", (sid,))
    await db.execute("UPDATE sets SET rpe = 10 WHERE workout_id = ? AND id = (SELECT MIN(id) FROM sets WHERE workout_id = ?)", (sid, sid))
    await db.commit()
    assert await _ready(db, [_item(bench)]) == {}
    await db.execute("UPDATE sets SET rpe = 9, notes = 'shoulder pain on the last rep' WHERE workout_id = ?", (sid,))
    await db.commit()
    assert await _ready(db, [_item(bench)]) == {}
    await db.execute("UPDATE sets SET notes = NULL WHERE workout_id = ?", (sid,))
    await db.commit()
    assert bench["id"] in await _ready(db, [_item(bench)])  # Hard (9) is fine; only Max blocks


@pytest.mark.asyncio
async def test_below_the_target_load_or_too_few_sets_is_not_ready(db):
    bench = await _ex(db, "Bench Press")
    await _session(db, 5, _sets(bench["id"], 57.5, 10))
    await _session(db, 2, _sets(bench["id"], 60, 10))
    assert await _ready(db, [_item(bench)]) == {}
    await _session(db, 1, _sets(bench["id"], 60, 10, n=2))
    assert await _ready(db, [_item(bench)]) == {}


@pytest.mark.asyncio
async def test_an_unfinished_session_does_not_count(db):
    bench = await _ex(db, "Bench Press")
    await _session(db, 5, _sets(bench["id"], 60, 10))
    await _session(db, 0, _sets(bench["id"], 60, 10), finished=False)
    assert await _ready(db, [_item(bench)]) == {}


@pytest.mark.asyncio
async def test_the_step_follows_the_plan_rule(db):
    squat = await _ex(db, "Back Squat")
    db_bench = await _ex(db, "Dumbbell Bench Press")
    for ago in (5, 2):
        await _session(db, ago, _sets(squat["id"], 100, 5) + _sets(db_bench["id"], 24, 12))
    r = await _ready(db, [_item(squat, reps="3-5", load_kg=100), _item(db_bench, reps="10-12", load_kg=24)])
    assert r[squat["id"]]["to_kg"] == 105
    assert r[db_bench["id"]]["to_kg"] == 26.5


@pytest.mark.asyncio
async def test_no_load_target_steps_up_from_the_lighter_session(db):
    bench = await _ex(db, "Bench Press")
    await _session(db, 5, _sets(bench["id"], 50, 10))
    await _session(db, 2, _sets(bench["id"], 52.5, 10))
    r = (await _ready(db, [_item(bench, load_kg=None)]))[bench["id"]]
    assert r["from_kg"] == 50 and r["to_kg"] == 52.5


@pytest.mark.asyncio
async def test_bodyweight_and_timed_holds_step_up_reps_and_seconds(db):
    pushup = await _ex(db, "Diamond Push-up")
    plank = await _ex(db, "Plank")
    for ago in (5, 2):
        await _session(db, ago, _sets(pushup["id"], 0, 12, added=0)
                       + [{"ex": plank["id"], "reps": 1, "secs": 45} for _ in range(3)])
    r = await _ready(db, [_item(pushup, reps="8-12", load_kg=None), _item(plank, reps="30-45s", load_kg=None)])
    assert r[pushup["id"]]["kind"] == "reps" and r[pushup["id"]]["to_reps"] == "9-13"
    assert r[plank["id"]]["kind"] == "secs" and r[plank["id"]]["to_reps"] == "40-55s"


@pytest.mark.asyncio
async def test_not_yet_hides_it_for_14_days(db):
    bench = await _ex(db, "Bench Press")
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    await progression.snooze(db, 1, bench["id"])
    assert await _ready(db, [_item(bench)]) == {}
    await db.execute("UPDATE progression_snoozes SET until = ? WHERE user_id = 1",
                     ((date.today() - timedelta(days=1)).isoformat(),))
    await db.commit()
    assert bench["id"] in await _ready(db, [_item(bench)])


@pytest.mark.asyncio
async def test_other_users_sessions_do_not_count(db, user_b):
    bench = await _ex(db, "Bench Press")
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10), uid=user_b["id"])
    assert await _ready(db, [_item(bench)]) == {}


@pytest.mark.asyncio
async def test_malformed_targets_give_no_suggestion(db):
    bench = await _ex(db, "Bench Press")
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    bad = [{"exercise_id": bench["id"], "name": "Bench Press", "equipment": "Barbell", "target": t}
           for t in (None, {}, {"sets": "x", "reps": "8-10"}, {"sets": 3, "reps": "AMRAP"})]
    assert await _ready(db, bad) == {}


# ── Plan page, Apply, Not yet, and the session's routine ───────────────

async def _saved_plan(client, db, bench) -> dict:
    r = await client.post("/coach/save", json={
        "title": "Push", "goal": "strength", "days_per_week": 1,
        "days": [{"focus": "Push", "exercises": [
            {"name": "Bench Press", "sets": 3, "reps": "8-10", "load_kg": 60, "rir": 2,
             "progression": "All sets at 10 reps with 2+ in reserve → add 2.5 kg (5 lb)."}]}],
    })
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_plan_page_offers_apply_and_not_yet(client, db):
    bench = await _ex(db, "Bench Press")
    plan = await _saved_plan(client, db, bench)
    page = (await client.get("/plan", headers=HTML)).text
    assert "Ready to progress" not in page
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    page = (await client.get("/plan", headers=HTML)).text
    assert "Ready to progress" in page
    assert f'data-plan-id="{plan["id"]}" data-exercise-id="{bench["id"]}"' in page


@pytest.mark.asyncio
async def test_apply_raises_the_plan_and_routine_target(client, db):
    bench = await _ex(db, "Bench Press")
    plan = await _saved_plan(client, db, bench)
    r = await client.post(f"/coach/plans/{plan['id']}/progress/{bench['id']}")
    assert r.status_code == 409                                  # not earned yet
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    r = await client.post(f"/coach/plans/{plan['id']}/progress/{bench['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["to_kg"] == 62.5
    async with db.execute("SELECT plan_json FROM coach_plans WHERE id = ?", (plan["id"],)) as cur:
        stored = json.loads((await cur.fetchone())["plan_json"])
    assert stored["days"][0]["exercises"][0]["load_kg"] == 62.5
    async with db.execute("SELECT target_json FROM routine_exercises WHERE routine_id = ?", (plan["routine_ids"][0],)) as cur:
        assert json.loads((await cur.fetchone())["target_json"])["load_kg"] == 62.5
    # At the new load the last two sessions no longer qualify: nothing to apply again.
    assert (await client.post(f"/coach/plans/{plan['id']}/progress/{bench['id']}")).status_code == 409
    page = (await client.get("/plan", headers=HTML)).text
    assert "Ready to progress" not in page


@pytest.mark.asyncio
async def test_apply_is_per_user(client, db, user_b_client):
    bench = await _ex(db, "Bench Press")
    plan = await _saved_plan(client, db, bench)
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    assert (await user_b_client.post(f"/coach/plans/{plan['id']}/progress/{bench['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_not_yet_endpoint(client, db):
    bench = await _ex(db, "Bench Press")
    await _saved_plan(client, db, bench)
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    r = await client.post(f"/coach/progress/{bench['id']}/snooze")
    assert r.status_code == 204
    assert "Ready to progress" not in (await client.get("/plan", headers=HTML)).text


@pytest.mark.asyncio
async def test_the_session_routine_carries_the_suggestion(client, db):
    bench = await _ex(db, "Bench Press")
    plan = await _saved_plan(client, db, bench)
    for ago in (5, 2):
        await _session(db, ago, _sets(bench["id"], 60, 10))
    routines = (await client.get("/routines")).json()
    mine = next(r for r in routines if r["id"] == plan["routine_ids"][0])
    target = mine["exercises"][0]["target"]
    assert target["ready"]["to_kg"] == 62.5
    # Library routines (no targets) are untouched.
    assert all(e.get("target") is None for r in routines if r["id"] != mine["id"] for e in r["exercises"])
