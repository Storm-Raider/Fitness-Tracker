"""
Training-profile builder extracted from app/routes/coach.py.

build_profile() is used by the AI coach route to summarise the user's recent
training history into a compact dict for the Ollama prompt.  Lives here so it
can be imported by other routes (e.g. /plan) without pulling in the full coach
router.
"""

import aiosqlite


async def build_profile(conn: aiosqlite.Connection, uid: int) -> dict:
    """Summarise the user's recent training for the coach prompt."""
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

    # Top estimated 1RMs (Epley) for loaded lifts.
    async with conn.execute(
        """
        SELECT e.name, MAX(ROUND(s.weight_kg * (1 + s.reps / 30.0), 1)) AS e1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ? AND s.weight_kg > 0
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

    # Neglected muscles = muscles the user has worked in the last 90 days
    # but has NOT touched in the last 7 days.
    async with conn.execute(
        """
        SELECT DISTINCT em.muscle
        FROM sets s
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-7 days')
        """,
        (uid,),
    ) as cur:
        recently_trained = {r["muscle"] for r in await cur.fetchall()}
    undertrained = [m for m in muscle_sets if m not in recently_trained]

    return {
        "total_workouts": freq["n"],
        "first_day": freq["first_day"],
        "last_day": freq["last_day"],
        "sessions_per_week": sessions_per_week,
        "top_exercises": top_exercises,
        "muscle_sets": muscle_sets,
        "undertrained": undertrained,
        "top_lifts": top_lifts,
    }
