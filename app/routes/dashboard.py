import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.charts import generate_muscle_bars, generate_weekly_bar_chart
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
        SELECT w.id, w.started_at, COUNT(s.id) AS set_count,
               CAST(ROUND((JULIANDAY('now','localtime') - JULIANDAY(w.started_at)) * 1440) AS INTEGER) AS elapsed_min
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        WHERE w.user_id = ? AND w.ended_at IS NULL
        GROUP BY w.id
        ORDER BY w.started_at DESC
        LIMIT 1
        """,
        (uid, uid),
    ) as cur:
        active_workout = await cur.fetchone()
        active_workout = dict(active_workout) if active_workout else None

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
        "SELECT weight_kg FROM body_metrics WHERE user_id = ? "
        "ORDER BY COALESCE(entry_date, DATE(recorded_at)) DESC, recorded_at DESC LIMIT 1",
        (uid,),
    ) as cur:
        _bw = await cur.fetchone()
    latest_bodyweight = _bw["weight_kg"] if _bw else None

    async with conn.execute(
        "SELECT weekly_goal_sessions FROM user_settings WHERE user_id = ?", (uid,)
    ) as cur:
        _gs = await cur.fetchone()
    weekly_goal = _gs["weekly_goal_sessions"] if _gs else None

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
        AND ROUND((JULIANDAY(ended_at) - JULIANDAY(started_at)) * 1440) <= 720
        """,
        (uid,),
    ) as cur:
        avg_duration_min = (await cur.fetchone())["avg_duration_min"]

    # 7-day daily volume for bar chart
    from datetime import date, timedelta
    today = date.today()
    window = [(today - timedelta(days=6 - i)).isoformat() for i in range(7)]
    async with conn.execute(
        """
        SELECT DATE(w.started_at, 'localtime') AS day,
               ROUND(SUM(s.weight_kg * s.reps), 1) AS volume_kg
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at, 'localtime') >= DATE('now', '-6 days', 'localtime')
        GROUP BY DATE(w.started_at, 'localtime')
        """,
        (uid,),
    ) as cur:
        _daily = {r["day"]: r["volume_kg"] for r in await cur.fetchall()}
    day_volumes = [(d, _daily.get(d, 0.0)) for d in window]
    weekly_bar_svg = generate_weekly_bar_chart(day_volumes)

    # Last-week aggregate for delta comparison
    async with conn.execute(
        """
        SELECT COALESCE(SUM(s.weight_kg * s.reps), 0) AS volume,
               COUNT(DISTINCT s.workout_id) AS sessions
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at, 'localtime') >= DATE('now', '-13 days', 'localtime')
          AND DATE(w.started_at, 'localtime') <  DATE('now', '-6 days',  'localtime')
        """,
        (uid,),
    ) as cur:
        _lw = dict(await cur.fetchone())
    last_week_volume   = _lw["volume"]
    last_week_sessions = _lw["sessions"]

    weekly_volume = vol_row["weekly_volume"]
    if last_week_volume and last_week_volume > 0:
        volume_delta_pct = round((weekly_volume - last_week_volume) / last_week_volume * 100)
    elif weekly_volume > 0:
        volume_delta_pct = None  # no prior data to compare
    else:
        volume_delta_pct = None

    # Muscle group volume breakdown for the past 7 days
    async with conn.execute(
        """
        SELECT em.muscle, ROUND(SUM(s.weight_kg * s.reps), 1) AS volume_kg
        FROM sets s
        JOIN workouts w  ON w.id  = s.workout_id
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        WHERE s.user_id = ?
          AND DATE(w.started_at, 'localtime') >= DATE('now', '-6 days', 'localtime')
        GROUP BY em.muscle
        ORDER BY volume_kg DESC
        LIMIT 8
        """,
        (uid,),
    ) as cur:
        muscle_vols = [(r["muscle"], r["volume_kg"]) for r in await cur.fetchall()]
    muscle_bar_svg = generate_muscle_bars(muscle_vols)

    # Recent PRs — exercises where the all-time max was matched within the last 30 days
    async with conn.execute(
        """
        WITH all_prs AS (
            SELECT exercise_id, MAX(weight_kg) AS max_weight
            FROM sets WHERE user_id = ?
            GROUP BY exercise_id
        ),
        recent_sets AS (
            SELECT s.exercise_id,
                   MAX(s.weight_kg)               AS recent_max,
                   MAX(DATE(w.started_at, 'localtime')) AS pr_date
            FROM sets s
            JOIN workouts w ON w.id = s.workout_id
            WHERE s.user_id = ?
              AND DATE(w.started_at, 'localtime') >= DATE('now', '-30 days', 'localtime')
            GROUP BY s.exercise_id
        )
        SELECT e.id AS exercise_id, e.name AS exercise_name,
               rs.recent_max AS pr_kg, rs.pr_date
        FROM recent_sets rs
        JOIN all_prs ap ON ap.exercise_id = rs.exercise_id
                        AND ap.max_weight  = rs.recent_max
        JOIN exercises e ON e.id = rs.exercise_id
        ORDER BY rs.pr_date DESC
        LIMIT 8
        """,
        (uid, uid),
    ) as cur:
        recent_prs = [dict(r) for r in await cur.fetchall()]

    return render(
        request,
        "dashboard",
        {
            "workouts": workouts,
            "prs": prs,
            "recent_prs": recent_prs,
            "heatmap_svg": heatmap_svg,
            "streak": streak,
            "weekly_volume": weekly_volume,
            "weekly_sessions": vol_row["weekly_sessions"],
            "total_workouts": total_workouts,
            "total_volume": total_volume,
            "avg_duration_min": avg_duration_min,
            "best_streak": best_streak,
            "active_workout": active_workout,
            "weekly_bar_svg": weekly_bar_svg,
            "muscle_bar_svg": muscle_bar_svg,
            "volume_delta_pct": volume_delta_pct,
            "last_week_sessions": last_week_sessions,
            "latest_bodyweight": latest_bodyweight,
            "weekly_goal": weekly_goal,
            "user": dict(current_user),
        },
    )
