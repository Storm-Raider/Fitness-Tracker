"""
Training-profile builder extracted from app/routes/coach.py.

build_profile() is used by the AI coach route to summarise the user's recent
training history into a compact dict for the Ollama prompt.  Lives here so it
can be imported by other routes (e.g. /plan) without pulling in the full coach
router.
"""

import time

import aiosqlite

# 30-minute TTL cache keyed by uid. Invalidated automatically on expiry.
# Fine-grained invalidation (e.g. on workout save) can call invalidate_profile().
_PROFILE_CACHE: dict[int, tuple[dict, float]] = {}
_PROFILE_TTL = 1800.0


def invalidate_profile(uid: int) -> None:
    _PROFILE_CACHE.pop(uid, None)


async def build_profile(conn: aiosqlite.Connection, uid: int) -> dict:
    """Summarise the user's recent training for the coach prompt."""
    _cached = _PROFILE_CACHE.get(uid)
    if _cached and (time.monotonic() - _cached[1]) < _PROFILE_TTL:
        return _cached[0]

    # Frequency / span (finished workouts only).
    async with conn.execute(
        """
        SELECT COUNT(*) AS n,
               MIN(DATE(started_at)) AS first_day,
               MAX(DATE(started_at)) AS last_day
        FROM workouts
        WHERE user_id = ? AND ended_at IS NOT NULL
        """,
        (uid,),
    ) as cur:
        freq = dict(await cur.fetchone())

    # Most-trained movements over the last 90 days.
    async with conn.execute(
        """
        SELECT e.name, e.category, COUNT(*) AS sets
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-90 days')
        GROUP BY e.id
        ORDER BY sets DESC
        LIMIT 15
        """,
        (uid,),
    ) as cur:
        top_exercises = [dict(r) for r in await cur.fetchall()]

    # Set count per primary muscle (coverage signal) over 90 days.
    async with conn.execute(
        """
        SELECT em.muscle, COUNT(*) AS sets
        FROM sets s
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-90 days')
        GROUP BY em.muscle
        ORDER BY sets DESC
        """,
        (uid,),
    ) as cur:
        muscle_sets = {r["muscle"]: r["sets"] for r in await cur.fetchall()}

    # Top estimated 1RMs (Epley) for loaded lifts. Bodyweight-equipment
    # exercises are excluded — this app stores weight_kg = bodyweight +
    # added weight for those (see workouts.py), so applying the Epley
    # formula to them produces a fictional "1RM" from bodyweight alone.
    async with conn.execute(
        """
        SELECT e.name, MAX(ROUND(s.weight_kg * (1 + s.reps / 30.0), 1)) AS e1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ? AND s.weight_kg > 0
          AND COALESCE(e.equipment, '') != 'Bodyweight'
        GROUP BY s.exercise_id
        ORDER BY e1rm DESC
        LIMIT 8
        """,
        (uid,),
    ) as cur:
        top_lifts = [dict(r) for r in await cur.fetchall()]

    # Sessions per week estimate.
    sessions_per_week = None
    if freq["n"] and freq["first_day"] and freq["last_day"]:
        async with conn.execute(
            "SELECT MAX(1, (JULIANDAY(?) - JULIANDAY(?)) / 7.0) AS weeks",
            (freq["last_day"], freq["first_day"]),
        ) as cur:
            weeks = (await cur.fetchone())["weeks"] or 1
        sessions_per_week = round(freq["n"] / weeks, 1)

    # Average weekly sets per muscle over the 90-day window (≈13 weeks).
    avg_weekly_sets = {m: round(n / 13.0, 1) for m, n in muscle_sets.items()}

    # Genuinely undertrained: muscles with < 50% of the peak muscle's weekly
    # volume, plus canonical muscles never touched in the 90-day window.
    _ALL_MUSCLES = {"Abs", "Back", "Biceps", "Chest", "Forearms", "Legs", "Shoulders", "Triceps"}
    if avg_weekly_sets:
        _peak = max(avg_weekly_sets.values())
        undertrained = sorted(
            {m for m, v in avg_weekly_sets.items() if _peak > 0 and v < _peak * 0.5}
            | (_ALL_MUSCLES - set(avg_weekly_sets))
        )
    else:
        undertrained = []

    # Most-used equipment types (top 3) — tells the coach what the athlete
    # actually has access to and prefers.
    async with conn.execute(
        """
        SELECT COALESCE(e.equipment, 'Other') AS equipment, COUNT(*) AS n
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-90 days')
        GROUP BY equipment
        ORDER BY n DESC
        LIMIT 3
        """,
        (uid,),
    ) as cur:
        preferred_equipment = [r["equipment"] for r in await cur.fetchall()]

    # Muscle recovery state — days since each primary muscle was last trained.
    async with conn.execute(
        """
        SELECT em.muscle,
               CAST(julianday('now','localtime') -
                    julianday(MAX(DATE(w.started_at,'localtime'))) AS INTEGER) AS days_ago
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        WHERE s.user_id = ?
        GROUP BY em.muscle
        """,
        (uid,),
    ) as cur:
        _recovery_rows = await cur.fetchall()

    muscle_recovery = {
        r["muscle"]: (
            "fatigued" if r["days_ago"] <= 1 else
            "recovering" if r["days_ago"] <= 3 else
            "fresh"
        )
        for r in _recovery_rows
    }

    # Stalled exercises — no meaningful 1RM progress in the last 28 days
    # vs. the 28–84-day window before that. Bodyweight-equipment exercises
    # are excluded for the same reason as the top-lifts query above.
    async with conn.execute(
        """
        SELECT e.name,
               COUNT(DISTINCT DATE(w.started_at,'localtime')) AS session_count,
               MAX(CASE WHEN DATE(w.started_at,'localtime') >= DATE('now','-28 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS recent_1rm,
               MAX(CASE WHEN DATE(w.started_at,'localtime') <  DATE('now','-28 days')
                        AND  DATE(w.started_at,'localtime') >= DATE('now','-84 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS prior_1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id AND w.ended_at IS NOT NULL
        WHERE s.user_id = ?
          AND COALESCE(e.equipment, '') != 'Bodyweight'
        GROUP BY s.exercise_id
        HAVING recent_1rm IS NOT NULL
           AND prior_1rm IS NOT NULL
           AND session_count >= 4
           AND recent_1rm <= prior_1rm * 1.02
        ORDER BY (prior_1rm - recent_1rm) DESC
        LIMIT 6
        """,
        (uid,),
    ) as cur:
        stalled = [r["name"] for r in await cur.fetchall()]

    # Average session duration — guides the model on how many exercises to include.
    async with conn.execute(
        """
        SELECT AVG((julianday(ended_at) - julianday(started_at)) * 1440) AS avg_min
        FROM workouts
        WHERE user_id = ? AND ended_at IS NOT NULL
          AND (julianday(ended_at) - julianday(started_at)) BETWEEN 0.01 AND 0.25
          AND DATE(started_at) >= DATE('now', '-90 days')
        """,
        (uid,),
    ) as cur:
        dur_row = await cur.fetchone()
    avg_session_minutes = round(dur_row["avg_min"]) if dur_row and dur_row["avg_min"] else None

    # Most recent bodyweight — for BW exercise load notation.
    async with conn.execute(
        """
        SELECT weight_kg FROM body_metrics
        WHERE user_id = ? AND weight_kg > 0
        ORDER BY recorded_at DESC LIMIT 1
        """,
        (uid,),
    ) as cur:
        bw_row = await cur.fetchone()
    bodyweight_kg = bw_row["weight_kg"] if bw_row else None

    # User-set strength targets — plan should progress toward these.
    async with conn.execute(
        """
        SELECT e.name, eg.target_kg
        FROM exercise_goals eg
        JOIN exercises e ON e.id = eg.exercise_id
        WHERE eg.user_id = ?
        ORDER BY eg.target_kg DESC
        LIMIT 6
        """,
        (uid,),
    ) as cur:
        exercise_goals = [dict(r) for r in await cur.fetchall()]

    # Feedback on the most recent coach plan — informs next generation's intensity.
    async with conn.execute(
        """
        SELECT feedback FROM coach_plans
        WHERE user_id = ? AND feedback IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (uid,),
    ) as cur:
        fb_row = await cur.fetchone()
    last_plan_feedback = fb_row["feedback"] if fb_row else None

    profile = {
        "total_workouts": freq["n"],
        "first_day": freq["first_day"],
        "last_day": freq["last_day"],
        "sessions_per_week": sessions_per_week,
        "avg_session_minutes": avg_session_minutes,
        "top_exercises": top_exercises,
        "muscle_sets": muscle_sets,
        "avg_weekly_sets": avg_weekly_sets,
        "undertrained": undertrained,
        "preferred_equipment": preferred_equipment,
        "top_lifts": top_lifts,
        "bodyweight_kg": bodyweight_kg,
        "exercise_goals": exercise_goals,
        "muscle_recovery": muscle_recovery,
        "stalled": stalled,
        "last_plan_feedback": last_plan_feedback,
    }
    _PROFILE_CACHE[uid] = (profile, time.monotonic())
    return profile
