"""Front-end quality pass (2026-11-02): Sessions by week, Analytics order and
empty state, Plan with the coach off, exercise history capped at 10."""
from datetime import date, timedelta

import pytest

from app.routes.workouts import _group_by_week
from app.utils.render import _day_label


def _w(d: date, kg: float = 100.0) -> dict:
    return {"started_at": f"{d.isoformat()} 08:00:00", "ended_at": "x", "volume_kg": kg}


def test_sessions_group_into_labelled_weeks_newest_first():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    weeks = _group_by_week([_w(today), _w(monday), _w(monday - timedelta(days=1), 50),
                            _w(monday - timedelta(days=15), 10)])
    assert [w["label"] for w in weeks][:2] == ["This week", "Last week"]
    assert len(weeks[0]["sessions"]) == 2 and weeks[0]["volume_kg"] == 200
    assert weeks[1]["volume_kg"] == 50
    assert "–" in weeks[2]["label"]           # "Oct 12 – 18" style


def test_day_label():
    assert _day_label(date.today().isoformat()) == "Today"
    assert _day_label("2026-10-30") == "Fri, Oct 30"


@pytest.mark.asyncio
async def test_sessions_page_shows_week_boxes(client):
    w = (await client.post("/workouts", json={})).json()["id"]
    ex = next(e for e in (await client.get("/api/exercises")).json()["exercises"] if e["name"] == "Bench Press")["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 60})
    await client.post(f"/workouts/{w}/finish")
    html = (await client.get("/workouts", headers={"Accept": "text/html"})).text
    assert 'class="wl-week"' in html and "This week" in html and f'id="workout-{w}"' in html


@pytest.mark.asyncio
async def test_analytics_empty_account_shows_one_empty_state(client):
    html = (await client.get("/analytics", headers={"Accept": "text/html"})).text
    assert "No sets logged yet" in html
    assert "Muscle volume" not in html and "Activity, last" not in html


@pytest.mark.asyncio
async def test_analytics_order_puts_records_last(client):
    w = (await client.post("/workouts", json={})).json()["id"]
    ex = next(e for e in (await client.get("/api/exercises")).json()["exercises"] if e["name"] == "Bench Press")["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 60})
    await client.post(f"/workouts/{w}/finish")
    html = (await client.get("/analytics", headers={"Accept": "text/html"})).text
    order = ["Muscle recovery", "Volume — last 12 weeks", "Activity, last", "Muscle volume",
             "Strength levels", "Personal records"]
    present = [t for t in order if t in html]
    assert present[0] == "Muscle recovery" and present[-1] == "Personal records", present
    assert [html.index(t) for t in present] == sorted(html.index(t) for t in present)


@pytest.mark.asyncio
async def test_plan_with_coach_off_offers_the_mesocycle(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    html = (await client.get("/plan", headers={"Accept": "text/html"})).text
    assert "AI routine (off)" in html and "Build a mesocycle instead" in html
    assert "setMode('meso');   // the coach is off" in html
