import json
import aiosqlite
from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.pr_utils import fetch_prs
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

    records = await fetch_prs(conn, uid)

    for r in records:
        r["time_ago"], r["days_ago"] = _time_ago(r["pr_date"])

    # Latest bodyweight for strength ratios
    async with conn.execute(
        "SELECT weight_kg FROM body_metrics WHERE user_id=? ORDER BY recorded_at DESC LIMIT 1",
        (uid,),
    ) as c:
        bw_row = await c.fetchone()
    bodyweight_kg = float(bw_row["weight_kg"]) if bw_row else None

    # 12-week e1RM trend per exercise (weekly max Epley 1RM)
    async with conn.execute(
        """
        SELECT s.exercise_id,
               strftime('%Y-%W', w.started_at) AS week,
               ROUND(MAX(s.weight_kg * (1 + s.reps / 30.0)), 1) AS e1rm
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-83 days')
        GROUP BY s.exercise_id, week
        ORDER BY s.exercise_id, week
        """,
        (uid,),
    ) as cur:
        _trend_rows = await cur.fetchall()

    trend_by_ex: dict[int, list] = {}
    for row in _trend_rows:
        trend_by_ex.setdefault(row["exercise_id"], []).append(row["e1rm"])
    trend_json = json.dumps({str(k): v for k, v in trend_by_ex.items()})

    # Stalled exercises: ≥4 sessions, no meaningful PR in last 3 weeks
    async with conn.execute(
        """
        SELECT s.exercise_id
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id AND w.ended_at IS NOT NULL
        WHERE s.user_id = ?
        GROUP BY s.exercise_id
        HAVING COUNT(DISTINCT DATE(w.started_at,'localtime')) >= 4
           AND MAX(CASE WHEN DATE(w.started_at,'localtime') >= DATE('now','-21 days')
                        THEN s.weight_kg * (1 + s.reps/30.0) END)
             <= MAX(CASE WHEN DATE(w.started_at,'localtime') < DATE('now','-21 days')
                        THEN s.weight_kg * (1 + s.reps/30.0) END) * 1.02
        """,
        (uid,),
    ) as cur:
        stalled_ids = {row["exercise_id"] for row in await cur.fetchall()}

    for r in records:
        r["stalled"] = r["id"] in stalled_ids

    return render(
        request,
        "prs",
        {
            "records": records,
            "bodyweight_kg": bodyweight_kg,
            "trend_json": trend_json,
            "user": dict(current_user),
        },
    )
