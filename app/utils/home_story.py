"""What Home leads with (the Instrument Sans redesign, 2026-10-31).

strength_story: the lift whose estimated one-rep max rose most over the last
12 weeks, with a straight-line projection to the next round target. A plain
least-squares fit over each session's best e1RM; no model call.

next_up: the next day of the newest saved coach plan, chosen as the day whose
exercises were trained longest ago (never trained comes first, then plan
order), with its targets and any weight suggestion from progression.ready.

Both return None when there is nothing honest to say; Home then shows less.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta

import aiosqlite

WINDOW_DAYS = 84            # 12 weeks
MIN_SESSIONS = 4            # fewer points than this is not a trend
MAX_ETA_DAYS = 365          # a projection further out than a year isn't shown


def _round_target(current: float) -> int:
    """The next multiple of 10 above the current e1RM, skipping one that is
    under 2 kg away (reaching it is not news)."""
    t = math.ceil(current / 10) * 10
    if t - current < 2:
        t += 10
    return int(t)


def _when(d: date) -> str:
    third = "early" if d.day <= 10 else "mid" if d.day <= 20 else "late"
    return f"{third}-{d.strftime('%B')}"


def _fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Least squares y = a + b*x; returns (a, b)."""
    n = len(points)
    sx = sum(x for x, _ in points); sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points); sxy = sum(x * y for x, y in points)
    den = n * sxx - sx * sx
    if den == 0:
        return sy / n, 0.0
    b = (n * sxy - sx * sy) / den
    return (sy - b * sx) / n, b


async def strength_story(conn: aiosqlite.Connection, uid: int, today: date | None = None) -> dict | None:
    today = today or date.today()
    start = today - timedelta(days=WINDOW_DAYS)
    async with conn.execute(
        """
        SELECT e.id, e.name, DATE(w.started_at) AS d, MAX(e1rm(s.weight_kg, s.reps)) AS best
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ? AND DATE(w.started_at) >= ? AND s.weight_kg > 0
          AND e.category NOT IN ('Cardio') AND COALESCE(e.equipment, '') != 'Bodyweight'
        GROUP BY e.id, d
        ORDER BY e.id, d
        """,
        (uid, start.isoformat()),
    ) as cur:
        rows = await cur.fetchall()

    series: dict[int, dict] = {}
    for r in rows:
        if r["best"] is None:
            continue
        s = series.setdefault(r["id"], {"name": r["name"], "pts": []})
        s["pts"].append((date.fromisoformat(r["d"]), float(r["best"])))

    best = None
    for ex_id, s in series.items():
        pts = s["pts"]
        if len(pts) < MIN_SESSIONS:
            continue
        first = max(v for d, v in pts[:2])          # the first two sessions, not one lucky day
        last = max(v for d, v in pts[-2:])
        gain = last - first
        if best is None or gain > best["gain"]:
            best = {"exercise_id": ex_id, "name": s["name"], "pts": pts, "gain": gain, "current": last}
    if best is None:
        return None

    pts = best["pts"]
    x0 = pts[0][0]
    a, b = _fit([((d - x0).days, v) for d, v in pts])
    story = {
        "exercise_id": best["exercise_id"],
        "name": best["name"],
        "since": pts[0][0].isoformat(),
        "since_label": pts[0][0].strftime("%B"),
        "gain": round(best["gain"]),
        "current": round(best["current"], 1),
        "points": [(d.isoformat(), round(v, 1)) for d, v in pts],
        "target": None, "eta": None, "eta_label": None,
    }
    if best["gain"] >= 1 and b > 0:
        target = _round_target(best["current"])
        days_needed = (target - (a + b * (today - x0).days)) / b
        if 0 < days_needed <= MAX_ETA_DAYS:
            eta = today + timedelta(days=round(days_needed))
            story.update(target=target, eta=eta.isoformat(), eta_label=_when(eta))
    return story


async def next_up(conn: aiosqlite.Connection, uid: int) -> dict | None:
    async with conn.execute(
        """SELECT id, plan_json FROM coach_plans
           WHERE user_id = ? AND (status IS NULL OR status = 'saved')
           ORDER BY created_at DESC LIMIT 1""",
        (uid,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    try:
        plan = json.loads(row["plan_json"] or "{}")
    except (ValueError, TypeError):
        return None
    days = plan.get("days") or []
    routine_ids = plan.get("routine_ids") or []
    if not days:
        return None

    # When each exercise was last trained.
    ids = sorted({ex.get("exercise_id") for d in days for ex in d.get("exercises", []) if ex.get("exercise_id")})
    last: dict[int, str] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        async with conn.execute(
            f"""SELECT s.exercise_id, MAX(DATE(w.started_at)) AS d FROM sets s
                JOIN workouts w ON w.id = s.workout_id
                WHERE s.user_id = ? AND s.exercise_id IN ({marks}) GROUP BY s.exercise_id""",
            (uid, *ids),
        ) as cur:
            last = {r["exercise_id"]: r["d"] for r in await cur.fetchall()}

    def day_last(d: dict) -> str:
        done = [last.get(ex.get("exercise_id")) for ex in d.get("exercises", [])]
        done = [x for x in done if x]
        return max(done) if done else ""        # "" sorts first: never trained

    idx = min(range(len(days)), key=lambda i: (day_last(days[i]), i))
    day = days[idx]

    ready: dict[int, dict] = {}
    try:
        from app.routes.coach import plan_items
        from app.utils import progression
        items = [it for it in await plan_items(conn, plan) if it["day"] == idx]
        ready = await progression.ready(conn, uid, items)
    except Exception:
        logging.warning("home: weight suggestions failed for uid=%d", uid, exc_info=True)

    lifts = []
    for ex in day.get("exercises", []):
        sug = ready.get(ex.get("exercise_id")) or {}
        load = sug.get("to_kg") or ex.get("load_kg")
        step = None
        if sug.get("to_kg") and sug.get("from_kg"):
            step = round(sug["to_kg"] - sug["from_kg"], 2)
        lifts.append({"name": ex.get("name", ""), "sets": ex.get("sets"), "reps": ex.get("reps"),
                      "load_kg": load, "step": step})
    return {
        "name": day.get("focus") or day.get("name") or f"Day {idx + 1}",
        "routine_id": routine_ids[idx] if idx < len(routine_ids) else None,
        "main": lifts[:2],
        "more": lifts[2:],
    }
