"""Workout times are stored in local time, so their date must not be converted again.

workouts.started_at defaults to datetime('now','localtime'). DATE(started_at,
'localtime') treats that local time as UTC and shifts it again, so in US
Central time a workout begun between midnight and 05:00 landed on the
previous day. "Today" must likewise be DATE('now','localtime'), not the UTC
date. These tests pin the process to America/Chicago so the shift is real
wherever they run.
"""
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from app.utils.challenges import training_dates
from app.utils.pr_utils import fetch_prs

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def central_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


async def _early_morning_workout(db, exercise_name="Bench Press"):
    """A finished workout from 01:30 to 02:30 today (local time)."""
    today = date.today().isoformat()
    async with db.execute(
        "INSERT INTO workouts(started_at, ended_at, user_id) VALUES (?, ?, 1)",
        (f"{today} 01:30:00", f"{today} 02:30:00"),
    ) as cur:
        wid = cur.lastrowid
    async with db.execute("SELECT id FROM exercises WHERE name = ?", (exercise_name,)) as cur:
        ex = (await cur.fetchone())["id"]
    await db.execute(
        "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) VALUES (?, ?, 5, 100, 1)",
        (wid, ex),
    )
    await db.commit()
    return today


@pytest.mark.asyncio
async def test_an_early_morning_workout_counts_for_today_in_challenges(db, central_time):
    today = await _early_morning_workout(db)
    assert today in await training_dates(db, 1)


@pytest.mark.asyncio
async def test_an_early_morning_pr_is_dated_today(db, central_time):
    today = await _early_morning_workout(db)
    [pr] = [p for p in await fetch_prs(db, 1) if p["name"] == "Bench Press"]
    assert pr["pr_date"] == today


@pytest.mark.asyncio
async def test_sql_today_is_the_local_date(db, central_time):
    async with db.execute("SELECT DATE('now','localtime') AS d") as cur:
        assert (await cur.fetchone())["d"] == datetime.now().date().isoformat()


def test_no_query_converts_a_local_column_again_or_uses_utc_today():
    """Every time column the app filters by date is stored in local time."""
    double = re.compile(r"(started_at|ended_at|logged_date|recorded_at|created_at)\s*,\s*'localtime'")
    utc_today = re.compile(r"""'now'(?![^)]*'localtime')""")   # any SQLite time function
    offenders = []
    for path in APP.rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if double.search(line) or (utc_today.search(line) and "DEFAULT" not in line):
                offenders.append(f"{path.relative_to(APP)}:{n}: {line.strip()}")
    assert offenders == []
