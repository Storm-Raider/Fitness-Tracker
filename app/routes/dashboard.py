from datetime import date, timedelta

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import challenges as ch
from app.utils.charts import generate_muscle_bars, generate_weekly_bar_chart
from app.utils.heatmap import generate_heatmap_svg
from app.utils.pr_utils import fetch_prs
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

    prs = await fetch_prs(conn, uid)
    today = date.today()
    recent_prs = sorted(
        [r for r in prs if r["pr_date"] and (today - date.fromisoformat(r["pr_date"])).days <= 30],
        key=lambda r: r["pr_date"],
        reverse=True,
    )[:8]

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
        SELECT DISTINCT day FROM (
            SELECT DATE(started_at,'localtime') AS day
            FROM workouts
            WHERE user_id = ? AND started_at >= date('now', '-364 days', 'localtime')
            UNION
            SELECT logged_date AS day
            FROM cardio_logs
            WHERE user_id = ? AND logged_date >= date('now', '-364 days', 'localtime')
        ) ORDER BY day
        """,
        (uid, uid),
    ) as cur:
        heatmap_dates = [r["day"] for r in await cur.fetchall()]

    heatmap_svg = generate_heatmap_svg(heatmap_dates)

    async with conn.execute(
        """
        SELECT DISTINCT day FROM (
            SELECT DATE(started_at,'localtime') AS day FROM workouts WHERE user_id = ?
            UNION
            SELECT logged_date AS day FROM cardio_logs WHERE user_id = ?
        ) ORDER BY day DESC
        """,
        (uid, uid),
    ) as cur:
        all_days = [r["day"] for r in await cur.fetchall()]

    streak = compute_streak(all_days)
    # all_days must remain unbounded for max_streak() accuracy
    best_streak = max_streak(all_days)

    async with conn.execute(
        """
        SELECT COALESCE(SUM(s.weight_kg * s.reps), 0) AS weekly_volume
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at,'localtime') >= DATE('now', '-6 days', 'localtime')
        """,
        (uid,),
    ) as cur:
        vol_row = dict(await cur.fetchone())

    async with conn.execute(
        """
        SELECT COUNT(*) AS weekly_sessions FROM (
            SELECT DISTINCT DATE(started_at,'localtime') AS day
            FROM workouts WHERE user_id = ?
            AND DATE(started_at,'localtime') >= DATE('now', '-6 days', 'localtime')
            UNION
            SELECT DISTINCT logged_date AS day FROM cardio_logs WHERE user_id = ?
            AND logged_date >= DATE('now', '-6 days', 'localtime')
        )
        """,
        (uid, uid),
    ) as cur:
        vol_row["weekly_sessions"] = (await cur.fetchone())["weekly_sessions"]

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
        SELECT COALESCE(SUM(s.weight_kg * s.reps), 0) AS volume
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at, 'localtime') >= DATE('now', '-13 days', 'localtime')
          AND DATE(w.started_at, 'localtime') <  DATE('now', '-6 days',  'localtime')
        """,
        (uid,),
    ) as cur:
        _lw = dict(await cur.fetchone())
    last_week_volume = _lw["volume"]

    async with conn.execute(
        """
        SELECT COUNT(*) AS sessions FROM (
            SELECT DISTINCT DATE(started_at,'localtime') AS day
            FROM workouts WHERE user_id = ?
            AND DATE(started_at,'localtime') >= DATE('now', '-13 days', 'localtime')
            AND DATE(started_at,'localtime') <  DATE('now', '-6 days',  'localtime')
            UNION
            SELECT DISTINCT logged_date AS day FROM cardio_logs WHERE user_id = ?
            AND logged_date >= DATE('now', '-13 days', 'localtime')
            AND logged_date <  DATE('now', '-6 days',  'localtime')
        )
        """,
        (uid, uid),
    ) as cur:
        last_week_sessions = (await cur.fetchone())["sessions"]

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

    # Active challenges (evaluated lazily — may flip to failed/completed here too)
    active_challenges = []
    today = ch.today_local()
    async with conn.execute(
        "SELECT * FROM challenge_attempts WHERE user_id=? AND status='active'", (uid,)
    ) as cur:
        _att = [dict(r) for r in await cur.fetchall()]
    if _att:
        _train = await ch.training_dates(conn, uid)
        for row in _att:
            v = await ch.evaluate_attempt(conn, row, today, _train)
            if v["status"] == "active":
                done, total = ch.rules_done_count(v, today)
                active_challenges.append({
                    "id": v["id"], "title": v["title"], "day_n": v["day_n"],
                    "total_days": v["total_days"], "today_done": done, "today_total": total,
                })

    # Today's daily log — used for dashboard nudge
    today_iso = today.isoformat()
    async with conn.execute(
        "SELECT id FROM daily_logs WHERE user_id=? AND log_date=?", (uid, today_iso)
    ) as cur:
        today_log_filled = (await cur.fetchone()) is not None

    return render(
        request,
        "dashboard",
        {
            "active_challenges": active_challenges,
            "today_log_filled": today_log_filled,
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
