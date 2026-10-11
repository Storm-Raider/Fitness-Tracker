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
