"""Home's lead (2026-10-31 redesign): the strength story and the next plan day."""
import json
from datetime import date, timedelta

import pytest

from app.utils import home_story
from app.utils.charts import generate_progress_plot


async def _bench_sessions(db, kgs, start: date, every: int = 7, reps: int = 5):
    ex = (await (await db.execute("SELECT id FROM exercises WHERE name='Bench Press'")).fetchone())["id"]
    for i, kg in enumerate(kgs):
        d = (start + timedelta(days=i * every)).isoformat()
        cur = await db.execute("INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
                               (f"{d} 08:00:00", f"{d} 09:00:00"))
        await db.execute("INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?, ?, ?, ?, 1)",
                         (cur.lastrowid, ex, reps, kg))
    await db.commit()
    return ex


@pytest.mark.asyncio
async def test_story_is_none_without_enough_sessions(db):
    await _bench_sessions(db, [60, 62.5, 65], date.today() - timedelta(days=21))
    assert await home_story.strength_story(db, 1) is None


@pytest.mark.asyncio
async def test_story_reports_gain_and_a_projection(db):
    today = date.today()
    await _bench_sessions(db, [60, 62.5, 65, 67.5, 70, 72.5], today - timedelta(days=35))
    s = await home_story.strength_story(db, 1, today)
    assert s["name"] == "Bench Press"
    # e1RM (Epley, 5 reps): 70 -> 84.6 first two best, 84.6.. vs last two best 72.5 -> 84.6
    assert s["gain"] >= 10 and s["current"] > 80
    assert s["target"] % 10 == 0 and s["target"] - s["current"] >= 2
    assert s["eta"] and date.fromisoformat(s["eta"]) > today
    assert s["eta_label"].split("-")[0] in ("early", "mid", "late")
    json.dumps(s)          # Home also answers JSON


@pytest.mark.asyncio
async def test_flat_lift_has_no_projection(db):
    today = date.today()
    await _bench_sessions(db, [80, 80, 80, 80, 80], today - timedelta(days=28))
    s = await home_story.strength_story(db, 1, today)
    assert s["gain"] == 0 and s["target"] is None and s["eta"] is None


def test_round_target_skips_one_too_close():
    assert home_story._round_target(84.6) == 90
    assert home_story._round_target(88.5) == 100      # 90 is under 2 kg away
    assert home_story._round_target(90.0) == 100


def test_progress_plot_text_is_label_sized_and_marks_today():
    svg = generate_progress_plot([("2026-08-01", 78), ("2026-09-01", 84), ("2026-10-30", 90)], 100, "2027-02-15")
    assert set(__import__("re").findall(r'font-size="(\d+)"', svg)) == {"13"}
    assert "#f5d547" in svg and 'stroke-dasharray="2 4"' in svg     # today marker, target datum
    assert generate_progress_plot([("2026-10-30", 90)]) == ""          # one point is not a line


@pytest.mark.asyncio
async def test_next_up_picks_the_day_trained_longest_ago(db):
    ex = await _bench_sessions(db, [60, 62.5], date.today() - timedelta(days=3))
    row = await (await db.execute("SELECT id FROM exercises WHERE name='Deadlift'")).fetchone()
    plan = {"days": [
        {"focus": "Push", "exercises": [{"exercise_id": ex, "name": "Bench Press", "sets": 3, "reps": "5", "load_kg": 62.5}]},
        {"focus": "Pull", "exercises": [{"exercise_id": row["id"], "name": "Deadlift", "sets": 3, "reps": "5", "load_kg": 100},
                                         {"exercise_id": None, "name": "Plank", "sets": 3, "reps": "30s"},
                                         {"exercise_id": None, "name": "Curl", "sets": 3, "reps": "10"}]},
    ], "routine_ids": [11, 12]}
    await db.execute("INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, status) VALUES (1,'B','strength',2,?,'saved')",
                     (json.dumps(plan),))
    await db.commit()
    up = await home_story.next_up(db, 1)
    assert up["name"] == "Pull" and up["routine_id"] == 12          # Deadlift never trained
    assert [l["name"] for l in up["main"]] == ["Deadlift", "Plank"] and len(up["more"]) == 1


@pytest.mark.asyncio
async def test_next_up_none_without_a_saved_plan(db):
    assert await home_story.next_up(db, 1) is None


@pytest.mark.asyncio
async def test_home_has_one_way_into_a_session_and_answers_json(client):
    html = (await client.get("/", headers={"Accept": "text/html"})).text
    assert html.count('class="start"') == 1
    r = await client.get("/")
    assert r.status_code == 200 and "week_days" in r.json()
