import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.heatmap import generate_heatmap_svg
from app.utils.render import render, templates
from app.utils.streak import compute_streak, max_streak

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    async with conn.execute(
        """
        SELECT w.id, w.started_at, w.ended_at, w.notes,
               COUNT(s.id) AS set_count
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        WHERE w.user_id = ?
        GROUP BY w.id
        ORDER BY w.started_at DESC
        LIMIT 7
        """,
        (uid, uid),
    ) as cur:
        workouts = [dict(r) for r in await cur.fetchall()]

    async with conn.execute(
        """
        SELECT e.id AS exercise_id, e.name AS exercise_name, MAX(s.weight_kg) AS pr_kg
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ?
        GROUP BY s.exercise_id
        ORDER BY e.name
        """,
        (uid,),
    ) as cur:
        prs = [dict(r) for r in await cur.fetchall()]

    async with conn.execute(
        """
        SELECT DISTINCT DATE(started_at) AS day
        FROM workouts
        WHERE user_id = ?
          AND started_at >= date('now', '-364 days')
        ORDER BY day
        """,
        (uid,),
    ) as cur:
        heatmap_dates = [r["day"] for r in await cur.fetchall()]

    heatmap_svg = generate_heatmap_svg(heatmap_dates)

    async with conn.execute(
        """
        SELECT DISTINCT DATE(started_at) AS day
        FROM workouts
        WHERE user_id = ?
        ORDER BY day DESC
        """,
        (uid,),
    ) as cur:
        all_days = [r["day"] for r in await cur.fetchall()]

    streak = compute_streak(all_days)
    # all_days must remain unbounded for max_streak() accuracy
    best_streak = max_streak(all_days)

    async with conn.execute(
        """
        SELECT COALESCE(SUM(s.weight_kg * s.reps), 0) AS weekly_volume,
               COUNT(DISTINCT s.workout_id)            AS weekly_sessions
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-6 days')
        """,
        (uid,),
    ) as cur:
        vol_row = dict(await cur.fetchone())

    async with conn.execute(
        "SELECT COUNT(*) AS total_workouts FROM workouts WHERE user_id = ?",
        (uid,),
    ) as cur:
        total_workouts = (await cur.fetchone())["total_workouts"]

    async with conn.execute(
        "SELECT COALESCE(SUM(weight_kg * reps), 0) AS total_volume FROM sets WHERE user_id = ?",
        (uid,),
    ) as cur:
        total_volume = (await cur.fetchone())["total_volume"]

    async with conn.execute(
        """
        SELECT CAST(ROUND(AVG(
            (JULIANDAY(ended_at) - JULIANDAY(started_at)) * 1440
        )) AS INTEGER) AS avg_duration_min
        FROM workouts WHERE user_id = ? AND ended_at IS NOT NULL
        """,
        (uid,),
    ) as cur:
        avg_duration_min = (await cur.fetchone())["avg_duration_min"]

    return render(
        request,
        "dashboard",
        {
            "workouts": workouts,
            "prs": prs,
            "heatmap_svg": heatmap_svg,
            "streak": streak,
            "weekly_volume": vol_row["weekly_volume"],
            "weekly_sessions": vol_row["weekly_sessions"],
            "total_workouts": total_workouts,
            "total_volume": total_volume,
            "avg_duration_min": avg_duration_min,
            "best_streak": best_streak,
            "user": dict(current_user),
        },
    )
