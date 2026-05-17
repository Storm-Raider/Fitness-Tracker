import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.charts import generate_sparkline
from app.utils.render import render

router = APIRouter()


@router.get("/stats", response_class=HTMLResponse)
async def stats(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    # Weekly volume — last 12 weeks, ascending so sparkline reads left→right
    async with conn.execute(
        """
        SELECT strftime('%Y-%W', w.started_at) AS week,
               COALESCE(SUM(s.weight_kg * s.reps), 0) AS volume
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        WHERE w.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-83 days')
        GROUP BY week
        ORDER BY week ASC
        """,
        (uid, uid),
    ) as cur:
        weekly_rows = [dict(r) for r in await cur.fetchall()]

    weekly_volumes = [r["volume"] for r in weekly_rows]
    weekly_labels = ["Wk " + r["week"].split("-")[1].lstrip("0") or "0" for r in weekly_rows]
    volume_sparkline = generate_sparkline(weekly_volumes, weekly_labels, unit=" kg")

    # Top 5 exercises by set count (all time)
    async with conn.execute(
        """
        SELECT e.name, COUNT(*) AS set_count
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ?
        GROUP BY s.exercise_id
        ORDER BY set_count DESC
        LIMIT 5
        """,
        (uid,),
    ) as cur:
        top_exercises = [dict(r) for r in await cur.fetchall()]

    # Muscle coverage this week (last 7 days)
    async with conn.execute(
        """
        SELECT em.muscle, em.is_primary, COUNT(DISTINCT w.id) AS sessions
        FROM workouts w
        JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id
        WHERE w.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-6 days')
        GROUP BY em.muscle, em.is_primary
        ORDER BY em.is_primary DESC, em.muscle ASC
        """,
        (uid, uid),
    ) as cur:
        muscle_rows = [dict(r) for r in await cur.fetchall()]

    primary_muscles = [r["muscle"] for r in muscle_rows if r["is_primary"]]
    secondary_muscles = [r["muscle"] for r in muscle_rows if not r["is_primary"]]

    return render(
        request,
        "stats",
        {
            "volume_sparkline": volume_sparkline,
            "weekly_rows": weekly_rows,
            "top_exercises": top_exercises,
            "primary_muscles": primary_muscles,
            "secondary_muscles": secondary_muscles,
            "user": dict(current_user),
        },
    )
