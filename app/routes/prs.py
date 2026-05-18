import aiosqlite
from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render

router = APIRouter()


def _time_ago(iso_date: str | None) -> tuple[str, int | None]:
    """Return (human label, days_ago) for an ISO date string."""
    if not iso_date:
        return "—", None
    days = (date.today() - date.fromisoformat(iso_date)).days
    if days == 0:
        label = "today"
    elif days == 1:
        label = "yesterday"
    elif days < 7:
        label = f"{days}d ago"
    elif days < 30:
        label = f"{days // 7}w ago"
    elif days < 365:
        label = f"{days // 30}mo ago"
    else:
        label = f"{days // 365}y ago"
    return label, days


@router.get("/prs", response_class=HTMLResponse)
async def personal_records(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    async with conn.execute(
        """
        WITH mx AS (
            SELECT exercise_id, MAX(weight_kg) AS pr_kg
            FROM sets WHERE user_id = ?
            GROUP BY exercise_id
        )
        SELECT e.id, e.name,
               mx.pr_kg,
               ROUND(mx.pr_kg * (1.0 + (
                   SELECT s2.reps FROM sets s2
                   JOIN workouts w2 ON w2.id = s2.workout_id
                   WHERE s2.exercise_id = e.id AND s2.user_id = ?
                     AND s2.weight_kg = mx.pr_kg
                   ORDER BY w2.started_at DESC LIMIT 1
               ) / 30.0), 1) AS est_1rm,
               (SELECT DATE(w2.started_at, 'localtime')
                FROM sets s2 JOIN workouts w2 ON w2.id = s2.workout_id
                WHERE s2.exercise_id = e.id AND s2.user_id = ?
                  AND s2.weight_kg = mx.pr_kg
                ORDER BY w2.started_at DESC LIMIT 1
               ) AS pr_date,
               COUNT(DISTINCT DATE(w.started_at, 'localtime')) AS sessions,
               COUNT(s.id) AS total_sets
        FROM mx
        JOIN exercises e ON e.id = mx.exercise_id
        JOIN sets s ON s.exercise_id = e.id AND s.user_id = ?
        JOIN workouts w ON w.id = s.workout_id
        GROUP BY e.id
        ORDER BY e.name
        """,
        (uid, uid, uid, uid),
    ) as cur:
        records = [dict(r) for r in await cur.fetchall()]

    for r in records:
        r["time_ago"], r["days_ago"] = _time_ago(r["pr_date"])

    return render(
        request,
        "prs",
        {"records": records, "user": dict(current_user)},
    )
