import pytest
from datetime import datetime, date, timedelta


@pytest.mark.asyncio
async def test_stats_renders(client):
    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Analytics" in resp.text
    # Always-rendered sections (data-driven sections only appear with data —
    # those are covered by test_stats_shows_top_exercise / _plateau).
    assert "Volume" in resp.text          # "Volume — last 12 weeks"
    assert "Muscle volume" in resp.text   # "Muscle volume — all time"


@pytest.mark.asyncio
async def test_stats_empty_state(client):
    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    # With no logged sessions, the volume trend shows its empty-state prompt.
    assert "Volume trend appears once" in resp.text
    assert "Start a session" in resp.text


@pytest.mark.asyncio
async def test_stats_empty_chart_state(client):
    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Volume trend appears once" in resp.text
    assert "Start a session" in resp.text
    # No volume chart is drawn when there's no data (the muscle-map SVG always
    # renders, so we check for the chart canvas specifically, not any <svg>).
    assert 'id="vol-chart"' not in resp.text


@pytest.mark.asyncio
async def test_stats_shows_top_exercise(client, db):
    ex = await client.post("/exercises", json={"name": "Stats Test Lift"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Stats Test Lift" in resp.text


@pytest.mark.asyncio
async def test_stats_shows_sparkline_with_two_weeks(client, db):
    ex = await client.post("/exercises", json={"name": "Stats Sparkline Lift"})
    ex_id = ex.json()["id"]

    # Two workouts in different weeks
    for days_ago in (14, 0):
        started = (datetime.now() - timedelta(days=days_ago)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, started_at) VALUES (1, ?)", (started,)
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM workouts WHERE started_at = ?", (started,)
        ) as cur:
            w_id = (await cur.fetchone())["id"]
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,5,80.0,1)",
            (w_id, ex_id),
        )
        await db.commit()

    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "<svg" in resp.text


@pytest.mark.asyncio
async def test_stats_shows_plateau(client, db):
    ex = await client.post("/exercises", json={"name": "Plateau Test Lift"})
    ex_id = ex.json()["id"]

    # Four sessions at 100 kg (route requires session_count >= 4 to flag a
    # stall): two prior (>28 days) and two recent, all at the same weight so
    # the estimated 1RM never improves.
    for days_ago in (40, 35, 28, 5):
        started = (datetime.now() - timedelta(days=days_ago)).isoformat()
        ended = (datetime.now() - timedelta(days=days_ago - 1)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
            (started, ended),
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM workouts WHERE started_at = ?", (started,)
        ) as cur:
            w_id = (await cur.fetchone())["id"]
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,5,100.0,1)",
            (w_id, ex_id),
        )
        await db.commit()

    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Plateau Test Lift" in resp.text
    assert "Stalled — no 1RM gain" in resp.text


@pytest.mark.asyncio
async def test_stats_no_plateau_when_improving(client, db):
    ex = await client.post("/exercises", json={"name": "Improving Lift"})
    ex_id = ex.json()["id"]

    # Old session at 80 kg, recent session at 90 kg — no plateau
    for days_ago, weight in [(30, 80.0), (3, 90.0)]:
        started = (datetime.now() - timedelta(days=days_ago)).isoformat()
        ended = (datetime.now() - timedelta(days=days_ago - 1)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
            (started, ended),
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM workouts WHERE started_at = ?", (started,)
        ) as cur:
            w_id = (await cur.fetchone())["id"]
        await db.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?,?,5,?,1)",
            (w_id, ex_id, weight),
        )
        await db.commit()

    resp = await client.get("/stats", headers={"Accept": "text/html"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Stalled — no 1RM gain" not in resp.text  # no Stalled section when improving
