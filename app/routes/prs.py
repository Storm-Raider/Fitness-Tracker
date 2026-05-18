import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render

router = APIRouter()


@router.get("/prs", response_class=HTMLResponse)
async def personal_records(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    async with conn.execute(
        """
        SELECT e.id, e.name,
               MAX(s.weight_kg) AS pr_kg,
               ROUND(MAX(s.weight_kg * (1.0 + s.reps / 30.0)), 1) AS est_1rm,
               COUNT(DISTINCT DATE(w.started_at)) AS sessions,
               COUNT(s.id) AS total_sets
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
        GROUP BY s.exercise_id
        ORDER BY e.name
        """,
        (uid,),
    ) as cur:
        records = [dict(r) for r in await cur.fetchall()]

    return render(
        request,
        "prs",
        {"records": records, "user": dict(current_user)},
    )
