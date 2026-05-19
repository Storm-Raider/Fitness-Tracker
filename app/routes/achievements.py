from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render
from app.utils.streak import max_streak

router = APIRouter()

ACHIEVEMENTS = [
    # Milestones — sets logged
    {"id": "first_set",   "name": "First Rep",      "desc": "Log your first set",          "icon": "zap",           "tier": "bronze"},
    {"id": "sets_100",    "name": "Centurion",       "desc": "Log 100 sets",                "icon": "hash",          "tier": "bronze"},
    {"id": "sets_1000",   "name": "Iron Veteran",    "desc": "Log 1,000 sets",              "icon": "layers",        "tier": "silver"},
    {"id": "sets_10000",  "name": "Legendary",       "desc": "Log 10,000 sets",             "icon": "infinity",      "tier": "gold"},
    # Weight lifted
    {"id": "lift_100kg",  "name": "Century Lift",    "desc": "Log a set with 100 kg+",      "icon": "dumbbell",      "tier": "bronze"},
    {"id": "lift_150kg",  "name": "Beast Mode",      "desc": "Log a set with 150 kg+",      "icon": "flame",         "tier": "silver"},
    {"id": "lift_200kg",  "name": "Elite",           "desc": "Log a set with 200 kg+",      "icon": "crown",         "tier": "gold"},
    # Volume
    {"id": "vol_10k",     "name": "Volume King",     "desc": "10,000 kg in one session",    "icon": "trending-up",   "tier": "silver"},
    {"id": "vol_1m",      "name": "Tonne Club",      "desc": "1,000,000 kg total volume",   "icon": "mountain",      "tier": "gold"},
    # Workouts completed
    {"id": "w_10",        "name": "Getting Started", "desc": "Complete 10 workouts",        "icon": "calendar",      "tier": "bronze"},
    {"id": "w_50",        "name": "Consistent",      "desc": "Complete 50 workouts",        "icon": "calendar-check","tier": "silver"},
    {"id": "w_100",       "name": "Dedicated",       "desc": "Complete 100 workouts",       "icon": "medal",         "tier": "gold"},
    # Streaks
    {"id": "streak_7",    "name": "Week Warrior",    "desc": "7-day training streak",       "icon": "activity",      "tier": "silver"},
    {"id": "streak_30",   "name": "Iron Discipline", "desc": "30-day training streak",      "icon": "shield",        "tier": "gold"},
    # PRs
    {"id": "prs_10",      "name": "PR Machine",      "desc": "Set 10 personal records",     "icon": "trophy",        "tier": "bronze"},
    {"id": "prs_50",      "name": "Record Breaker",  "desc": "Set 50 personal records",     "icon": "star",          "tier": "silver"},
    # Variety
    {"id": "variety_7",   "name": "Variety Pack",    "desc": "7 different exercises in one session", "icon": "shuffle", "tier": "bronze"},
    # Feature usage
    {"id": "template",    "name": "Planner",         "desc": "Save a workout template",     "icon": "layout-template","tier": "bronze"},
    {"id": "cardio",      "name": "All-Rounder",     "desc": "Log a cardio session",        "icon": "wind",          "tier": "bronze"},
    {"id": "measurement", "name": "Body Tracker",    "desc": "Log a body measurement",      "icon": "ruler",         "tier": "bronze"},
    # Body composition
    {"id": "bw_log_7",    "name": "Scale Watcher",   "desc": "Log bodyweight 7 times",      "icon": "scale",         "tier": "bronze"},
    # Session length
    {"id": "long_session","name": "Marathon Session","desc": "Complete a 2-hour workout",   "icon": "clock",         "tier": "silver"},
]

_ACH_INDEX = {a["id"]: a for a in ACHIEVEMENTS}


async def _compute_earned(conn: aiosqlite.Connection, uid: int) -> dict[str, str]:
    """Return {achievement_id: iso_datetime} for each achievement currently earned."""
    earned: dict[str, str] = {}
    now = datetime.now().isoformat(timespec="seconds")

    # Sets count
    async with conn.execute("SELECT COUNT(*) AS n FROM sets WHERE user_id=?", (uid,)) as c:
        n_sets = (await c.fetchone())["n"]
    if n_sets >= 1:     earned["first_set"]  = now
    if n_sets >= 100:   earned["sets_100"]   = now
    if n_sets >= 1000:  earned["sets_1000"]  = now
    if n_sets >= 10000: earned["sets_10000"] = now

    # Max weight per set
    async with conn.execute("SELECT MAX(weight_kg) AS m FROM sets WHERE user_id=?", (uid,)) as c:
        max_kg = (await c.fetchone())["m"] or 0
    if max_kg >= 100: earned["lift_100kg"] = now
    if max_kg >= 150: earned["lift_150kg"] = now
    if max_kg >= 200: earned["lift_200kg"] = now

    # Max session volume
    async with conn.execute(
        "SELECT MAX(v) AS m FROM (SELECT SUM(weight_kg*reps) AS v FROM sets WHERE user_id=? GROUP BY workout_id)",
        (uid,),
    ) as c:
        msv = (await c.fetchone())["m"] or 0
    if msv >= 10000: earned["vol_10k"] = now

    # Total volume
    async with conn.execute("SELECT SUM(weight_kg*reps) AS v FROM sets WHERE user_id=?", (uid,)) as c:
        total_vol = (await c.fetchone())["v"] or 0
    if total_vol >= 1_000_000: earned["vol_1m"] = now

    # Finished workouts
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM workouts WHERE user_id=? AND ended_at IS NOT NULL", (uid,)
    ) as c:
        n_w = (await c.fetchone())["n"]
    if n_w >= 10:  earned["w_10"]  = now
    if n_w >= 50:  earned["w_50"]  = now
    if n_w >= 100: earned["w_100"] = now

    # Max streak
    async with conn.execute(
        "SELECT DISTINCT DATE(started_at) AS day FROM workouts WHERE user_id=? ORDER BY day DESC", (uid,)
    ) as c:
        all_days = [r["day"] for r in await c.fetchall()]
    ms = max_streak(all_days)
    if ms >= 7:  earned["streak_7"]  = now
    if ms >= 30: earned["streak_30"] = now

    # PR count via window function
    async with conn.execute(
        """
        WITH sm AS (
            SELECT s.exercise_id, DATE(w.started_at,'localtime') AS d, MAX(s.weight_kg) AS mk
            FROM sets s JOIN workouts w ON w.id=s.workout_id
            WHERE s.user_id=? GROUP BY s.exercise_id, DATE(w.started_at,'localtime')
        ), rm AS (
            SELECT *, MAX(mk) OVER (PARTITION BY exercise_id ORDER BY d
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS pm FROM sm
        )
        SELECT COUNT(*) AS n FROM rm WHERE mk > COALESCE(pm, -1)
        """,
        (uid,),
    ) as c:
        n_prs = (await c.fetchone())["n"]
    if n_prs >= 10: earned["prs_10"] = now
    if n_prs >= 50: earned["prs_50"] = now

    # 7 exercises in one session
    async with conn.execute(
        """
        SELECT MAX(cnt) AS m FROM (
            SELECT workout_id, COUNT(DISTINCT exercise_id) AS cnt
            FROM sets WHERE user_id=? GROUP BY workout_id
        )
        """,
        (uid,),
    ) as c:
        max_ex = (await c.fetchone())["m"] or 0
    if max_ex >= 7: earned["variety_7"] = now

    # Templates
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM workout_templates WHERE user_id=?", (uid,)
    ) as c:
        if (await c.fetchone())["n"] >= 1: earned["template"] = now

    # Cardio
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM cardio_logs WHERE user_id=?", (uid,)
    ) as c:
        if (await c.fetchone())["n"] >= 1: earned["cardio"] = now

    # Body measurements
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM body_measurements WHERE user_id=?", (uid,)
    ) as c:
        if (await c.fetchone())["n"] >= 1: earned["measurement"] = now

    # Bodyweight logged 7 times
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM body_metrics WHERE user_id=?", (uid,)
    ) as c:
        if (await c.fetchone())["n"] >= 7: earned["bw_log_7"] = now

    # 2-hour session
    async with conn.execute(
        """
        SELECT MAX(ROUND((JULIANDAY(ended_at)-JULIANDAY(started_at))*1440)) AS m
        FROM workouts WHERE user_id=? AND ended_at IS NOT NULL
        """,
        (uid,),
    ) as c:
        max_dur = (await c.fetchone())["m"] or 0
    if max_dur >= 120: earned["long_session"] = now

    return earned


@router.get("/achievements", response_class=HTMLResponse)
async def achievements_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    earned_now = await _compute_earned(conn, uid)

    # Load already-stored unlock times
    async with conn.execute(
        "SELECT achievement_id, unlocked_at FROM user_achievements WHERE user_id=?", (uid,)
    ) as c:
        stored = {r["achievement_id"]: r["unlocked_at"] for r in await c.fetchall()}

    # Persist newly earned
    newly = {aid: ts for aid, ts in earned_now.items() if aid not in stored}
    for aid, ts in newly.items():
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO user_achievements(user_id, achievement_id, unlocked_at) VALUES (?,?,?)",
                (uid, aid, ts),
            )
        except Exception:
            pass
    if newly:
        await conn.commit()
        stored.update(newly)

    # Build display list
    achievements = []
    for ach in ACHIEVEMENTS:
        unlocked_at = stored.get(ach["id"])
        achievements.append({**ach, "unlocked": bool(unlocked_at), "unlocked_at": unlocked_at})

    unlocked_count = sum(1 for a in achievements if a["unlocked"])

    return render(
        request,
        "achievements",
        {
            "achievements": achievements,
            "unlocked_count": unlocked_count,
            "total_count": len(ACHIEVEMENTS),
            "user": dict(current_user),
        },
    )
