"""Home's weekly recap: last week (Monday to Sunday) in one line.

Home's other numbers are a rolling seven days; this one is the finished
calendar week, shown Monday to Wednesday so a week that starts without opening
the app still sees it."""
from datetime import date, timedelta

import aiosqlite

from app.utils.oura import band
from app.utils.pr_utils import fetch_prs

SHOW_UNTIL_WEEKDAY = 2   # Wednesday (Monday is 0)


def _today() -> date:
    return date.today()


def _range_label(start: date, end: date) -> str:
    """"Oct 5 – 11", or "Sep 28 – Oct 4" across a month."""
    if start.month == end.month:
        return f"{start:%b} {start.day} – {end.day}"
    return f"{start:%b} {start.day} – {end:%b} {end.day}"


async def _sessions(conn, uid: int, start: str, end: str) -> int:
    """Training days, lifting or cardio, like Home's "Sessions this week"."""
    async with conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT DATE(started_at) AS day FROM workouts
            WHERE user_id = ? AND DATE(started_at) BETWEEN ? AND ?
            UNION
            SELECT logged_date AS day FROM cardio_logs
            WHERE user_id = ? AND logged_date BETWEEN ? AND ?
        )
        """,
        (uid, start, end, uid, start, end),
    ) as cur:
        return (await cur.fetchone())["n"]


async def _volume(conn, uid: int, start: str, end: str) -> float:
    async with conn.execute(
        """
        SELECT COALESCE(SUM(s.weight_kg * s.reps), 0) AS v
        FROM sets s JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ? AND DATE(w.started_at) BETWEEN ? AND ?
        """,
        (uid, start, end),
    ) as cur:
        return (await cur.fetchone())["v"]


async def last_week_recap(conn: aiosqlite.Connection, uid: int, today: date | None = None) -> dict | None:
    """Last week's sessions, volume (and change on the week before), records
    still standing from it, and average Oura readiness. None from Thursday to
    Sunday, and for someone who has never logged a session."""
    today = today or _today()
    if today.weekday() > SHOW_UNTIL_WEEKDAY:
        return None
    async with conn.execute(
        "SELECT 1 FROM workouts WHERE user_id = ? AND DATE(started_at) < ? LIMIT 1",
        (uid, today.isoformat()),
    ) as cur:
        if not await cur.fetchone():
            return None

    monday = today - timedelta(days=today.weekday())
    start, end = monday - timedelta(days=7), monday - timedelta(days=1)
    prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=1)
    s, e = start.isoformat(), end.isoformat()

    volume = await _volume(conn, uid, s, e)
    prev_volume = await _volume(conn, uid, prev_start.isoformat(), prev_end.isoformat())
    delta = round((volume - prev_volume) / prev_volume * 100) if prev_volume > 0 else None

    records = sum(1 for r in await fetch_prs(conn, uid) if r["pr_date"] and s <= r["pr_date"] <= e)

    async with conn.execute(
        "SELECT AVG(readiness_score) AS r FROM oura_daily WHERE user_id = ? AND day BETWEEN ? AND ?",
        (uid, s, e),
    ) as cur:
        avg = (await cur.fetchone())["r"]
    readiness = round(avg) if avg is not None else None

    return {
        "week_start": s,
        "week_end": e,
        "label": _range_label(start, end),
        "sessions": await _sessions(conn, uid, s, e),
        "volume_kg": volume,
        "volume_delta_pct": delta,
        "records": records,
        "readiness": readiness,
        "readiness_band": band(readiness),
    }
