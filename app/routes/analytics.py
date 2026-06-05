import json
import aiosqlite
from datetime import date as _date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.pr_utils import fetch_prs
from app.utils.render import render

router = APIRouter()


def _time_ago(iso_date: str | None) -> tuple[str, int | None]:
    if not iso_date:
        return "—", None
    days = (_date.today() - _date.fromisoformat(iso_date)).days
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


def _week_label(year_week: str) -> str:
    y, w = year_week.split("-")
    try:
        d = datetime.strptime(f"{y} {int(w):02d} 1", "%Y %W %w")
        return d.strftime("%b %d").replace(" 0", " ")
    except ValueError:
        return year_week


def _week_start(year_week: str) -> str:
    y, w = year_week.split("-")
    try:
        d = datetime.strptime(f"{y} {int(w):02d} 1", "%Y %W %w")
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return ""


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    # ── Records (PR table + percentiles) ─────────────────────────────
    records = await fetch_prs(conn, uid)
    for r in records:
        r["time_ago"], r["days_ago"] = _time_ago(r["pr_date"])

    async with conn.execute(
        "SELECT weight_kg FROM body_metrics WHERE user_id=? ORDER BY recorded_at DESC LIMIT 1",
        (uid,),
    ) as c:
        bw_row = await c.fetchone()
    bodyweight_kg = float(bw_row["weight_kg"]) if bw_row else None

    # 12-week e1RM trend per exercise (sparklines)
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

    # Plateau marker for PR table rows
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

    # ── Volume trend (12 weeks) ───────────────────────────────────────
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

    today = _date.today()
    monday = today - timedelta(days=today.weekday())
    all_weeks = [
        (monday - timedelta(weeks=i)).strftime("%Y-%W")
        for i in range(11, -1, -1)
    ]
    _actual = {r["week"]: r["volume"] for r in weekly_rows}
    weekly_json = json.dumps([
        {
            "label": _week_label(w),
            "volume": _actual.get(w, 0),
            "week_start": _week_start(w),
            "cardio": _cardio.get(w, 0),
        }
        for w in all_weeks
    ])

    # ── Muscle recovery ───────────────────────────────────────────────
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

    # ── Stalled exercises (dedicated section) ────────────────────────
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

    # ── Muscle heatmap ────────────────────────────────────────────────
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
    muscle_data_json = json.dumps(
        {m["muscle"]: round(m["volume"] or 0, 1) for m in muscle_volumes}
    )

    return render(
        request,
        "analytics",
        {
            "records": records,
            "bodyweight_kg": bodyweight_kg,
            "trend_json": trend_json,
            "weekly_json": weekly_json,
            "weekly_rows": weekly_rows,
            "has_cardio": any(_cardio.values()),
            "muscle_recovery": muscle_recovery,
            "stalled": stalled,
            "muscle_data_json": muscle_data_json,
            "user": dict(current_user),
        },
    )
