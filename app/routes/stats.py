import json
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render

router = APIRouter()


def _week_label(year_week: str) -> str:
    y, w = year_week.split("-")
    try:
        d = datetime.strptime(f"{y} {int(w):02d} 1", "%Y %W %w")
        return d.strftime("%b %d").replace(" 0", " ")
    except Exception:
        return year_week


def _week_start(year_week: str) -> str:
    y, w = year_week.split("-")
    try:
        d = datetime.strptime(f"{y} {int(w):02d} 1", "%Y %W %w")
        return d.strftime("%Y-%m-%d")
    except Exception:
        return ""


@router.get("/stats", response_class=HTMLResponse)
async def stats(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    # 12-week volume trend
    async with conn.execute(
        """
        SELECT strftime('%Y-%W', w.started_at) AS week,
               COALESCE(SUM(s.weight_kg * s.reps), 0) AS volume
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        WHERE w.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-83 days')
        GROUP BY week ORDER BY week ASC
        """,
        (uid, uid),
    ) as cur:
        weekly_rows = [dict(r) for r in await cur.fetchall()]

    # Cardio sessions per week for the same window
    async with conn.execute(
        """
        SELECT strftime('%Y-%W', w.started_at) AS week,
               COUNT(c.id) AS cardio_count
        FROM cardio_logs c
        JOIN workouts w ON w.id = c.workout_id
        WHERE c.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-83 days')
        GROUP BY week
        """,
        (uid,),
    ) as cur:
        _cardio = {r["week"]: r["cardio_count"] for r in await cur.fetchall()}

    weekly_json = json.dumps([
        {
            "label": _week_label(r["week"]),
            "volume": r["volume"],
            "week_start": _week_start(r["week"]),
            "cardio": _cardio.get(r["week"], 0),
        }
        for r in weekly_rows
    ])

    # Muscle recovery — days since last primary-muscle session
    async with conn.execute(
        """
        SELECT em.muscle,
               MAX(DATE(w.started_at, 'localtime')) AS last_date,
               CAST(julianday('now','localtime') -
                    julianday(MAX(DATE(w.started_at,'localtime'))) AS INTEGER) AS days_ago
        FROM sets s
        JOIN workouts w  ON w.id  = s.workout_id
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        WHERE s.user_id = ?
        GROUP BY em.muscle
        ORDER BY days_ago ASC
        """,
        (uid,),
    ) as cur:
        muscle_recovery = [dict(r) for r in await cur.fetchall()]

    # PR timeline — every session that set a new exercise max (window function, SQLite ≥ 3.25)
    async with conn.execute(
        """
        WITH sm AS (
            SELECT s.exercise_id, e.id AS ex_id, e.name AS exercise_name,
                   DATE(w.started_at, 'localtime') AS session_date,
                   MAX(s.weight_kg) AS max_kg
            FROM sets s
            JOIN exercises e ON e.id = s.exercise_id
            JOIN workouts w  ON w.id = s.workout_id
            WHERE s.user_id = ?
            GROUP BY s.exercise_id, DATE(w.started_at, 'localtime')
        ),
        rm AS (
            SELECT *,
                   MAX(max_kg) OVER (
                       PARTITION BY exercise_id ORDER BY session_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS prev_max
            FROM sm
        )
        SELECT exercise_id, exercise_name, session_date AS date, max_kg
        FROM rm
        WHERE max_kg > COALESCE(prev_max, -1)
        ORDER BY session_date DESC
        LIMIT 120
        """,
        (uid,),
    ) as cur:
        pr_timeline = [dict(r) for r in await cur.fetchall()]

    # Stalled exercises — est 1RM not improved in 4 weeks vs prior 4-12 weeks
    async with conn.execute(
        """
        SELECT e.id, e.name,
               MAX(CASE WHEN DATE(w.started_at,'localtime') >= DATE('now','-28 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS recent_1rm,
               MAX(CASE WHEN DATE(w.started_at,'localtime') <  DATE('now','-28 days')
                        AND  DATE(w.started_at,'localtime') >= DATE('now','-84 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS prior_1rm,
               COUNT(DISTINCT DATE(w.started_at,'localtime')) AS session_count
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w  ON w.id = s.workout_id AND w.ended_at IS NOT NULL
        WHERE s.user_id = ?
        GROUP BY s.exercise_id
        HAVING recent_1rm IS NOT NULL
           AND prior_1rm IS NOT NULL
           AND session_count >= 4
           AND recent_1rm <= prior_1rm * 1.02
        ORDER BY (prior_1rm - recent_1rm) DESC
        LIMIT 8
        """,
        (uid,),
    ) as cur:
        stalled = [dict(r) for r in await cur.fetchall()]

    # Top 5 exercises by set count
    async with conn.execute(
        """
        SELECT e.name, COUNT(*) AS set_count
        FROM sets s JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ?
        GROUP BY s.exercise_id ORDER BY set_count DESC LIMIT 5
        """,
        (uid,),
    ) as cur:
        top_exercises = [dict(r) for r in await cur.fetchall()]

    # Sets per primary muscle this week
    async with conn.execute(
        """
        SELECT em.muscle, COUNT(s.id) AS set_count
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id AND w.user_id = ?
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-6 days')
        GROUP BY em.muscle ORDER BY set_count DESC
        """,
        (uid, uid),
    ) as cur:
        weekly_muscle_sets = [dict(r) for r in await cur.fetchall()]

    # All-time muscle volume for body heatmap
    async with conn.execute(
        """
        SELECT em.muscle, SUM(s.weight_kg * s.reps) AS volume
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id AND w.user_id = ?
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        WHERE s.user_id = ?
        GROUP BY em.muscle
        """,
        (uid, uid),
    ) as cur:
        muscle_volumes = [dict(r) for r in await cur.fetchall()]
    muscle_data_json = json.dumps({m["muscle"]: round(m["volume"] or 0, 1) for m in muscle_volumes})

    return render(
        request,
        "stats",
        {
            "weekly_json": weekly_json,
            "weekly_rows": weekly_rows,
            "has_cardio": any(_cardio.values()),
            "muscle_recovery": muscle_recovery,
            "pr_timeline": pr_timeline,
            "stalled": stalled,
            "top_exercises": top_exercises,
            "weekly_muscle_sets": weekly_muscle_sets,
            "muscle_data_json": muscle_data_json,
            "user": dict(current_user),
        },
    )
