import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.utils.heatmap import generate_heatmap_svg
from app.utils.render import render, templates
from app.utils.streak import compute_streak

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, conn: aiosqlite.Connection = Depends(get_db)
):
    # Recent workouts (last 7)
    async with conn.execute(
        """
        SELECT w.id, w.started_at, w.ended_at, w.notes,
               COUNT(s.id) AS set_count
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = 1
        WHERE w.user_id = 1
        GROUP BY w.id
        ORDER BY w.started_at DESC
        LIMIT 7
        """
    ) as cur:
        workouts = [dict(r) for r in await cur.fetchall()]

    # Personal records (MAX weight per exercise)
    async with conn.execute(
        """
        SELECT e.name AS exercise_name, MAX(s.weight_kg) AS pr_kg
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = 1
        GROUP BY s.exercise_id
        ORDER BY e.name
        """
    ) as cur:
        prs = [dict(r) for r in await cur.fetchall()]

    # 52-week heatmap (filter in SQL)
    async with conn.execute(
        """
        SELECT DISTINCT DATE(started_at) AS day
        FROM workouts
        WHERE user_id = 1
          AND started_at >= date('now', '-364 days')
        ORDER BY day
        """
    ) as cur:
        heatmap_dates = [r["day"] for r in await cur.fetchall()]

    heatmap_svg = generate_heatmap_svg(heatmap_dates)

    # Streak counter
    async with conn.execute(
        """
        SELECT DISTINCT DATE(started_at) AS day
        FROM workouts
        WHERE user_id = 1
        ORDER BY day DESC
        """
    ) as cur:
        all_days = [r["day"] for r in await cur.fetchall()]

    streak = compute_streak(all_days)

    # Volume + session count — rolling 7-day window
    async with conn.execute(
        """
        SELECT COALESCE(SUM(s.weight_kg * s.reps), 0) AS weekly_volume,
               COUNT(DISTINCT s.workout_id)            AS weekly_sessions
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = 1
          AND DATE(w.started_at) >= DATE('now', '-6 days')
        """
    ) as cur:
        vol_row = dict(await cur.fetchone())

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
        },
    )
