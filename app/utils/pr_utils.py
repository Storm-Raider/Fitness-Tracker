import aiosqlite


def epley(weight_kg: float | None, reps: float | None) -> float | None:
    """Estimated one-rep max (Epley): weight × (1 + reps / 30).

    The single definition of the formula. open_db() registers it as the SQL
    function e1rm(weight, reps), so queries call e1rm() instead of writing it
    out; the client-side copies in exercise_detail.html and workout_form.html
    must match it.
    """
    if weight_kg is None or reps is None:
        return None
    return weight_kg * (1 + reps / 30.0)


async def stalled_lifts(conn: aiosqlite.Connection, uid: int, limit: int) -> list[dict]:
    """Lifts with no meaningful e1RM progress: the best e1RM in the last 28 days
    is within 2% of (or below) the best in the 28–84 days before, over at
    least 4 sessions. Shared by the analytics page and the coach's profile.

    Bodyweight-equipment exercises are left out: their weight_kg is the
    lifter's bodyweight (plus any added load), so a flat "e1RM" says nothing.
    """
    async with conn.execute(
        """
        SELECT e.id, e.name,
               MAX(CASE WHEN DATE(w.started_at) >= DATE('now','localtime','-28 days')
                        THEN ROUND(e1rm(s.weight_kg, s.reps), 1) END) AS recent_1rm,
               MAX(CASE WHEN DATE(w.started_at) <  DATE('now','localtime','-28 days')
                        AND  DATE(w.started_at) >= DATE('now','localtime','-84 days')
                        THEN ROUND(e1rm(s.weight_kg, s.reps), 1) END) AS prior_1rm,
               COUNT(DISTINCT DATE(w.started_at)) AS session_count
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w  ON w.id = s.workout_id AND w.ended_at IS NOT NULL
        WHERE s.user_id = ?
          AND COALESCE(e.equipment, '') != 'Bodyweight'
        GROUP BY s.exercise_id
        HAVING recent_1rm IS NOT NULL
           AND prior_1rm IS NOT NULL
           AND session_count >= 4
           AND recent_1rm <= prior_1rm * 1.02
        ORDER BY (prior_1rm - recent_1rm) DESC
        LIMIT ?
        """,
        (uid, limit),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def fetch_prs(conn: aiosqlite.Connection, uid: int) -> list[dict]:
    """All-time PRs per exercise with est. 1RM, pr_date, sessions, and total_sets."""
    async with conn.execute(
        """
        WITH mx AS (
            SELECT exercise_id, MAX(weight_kg) AS pr_kg
            FROM sets WHERE user_id = ? AND weight_kg > 0
            GROUP BY exercise_id
        )
        SELECT e.id,
               e.name,
               mx.pr_kg,
               ROUND(e1rm(mx.pr_kg, (
                   SELECT s2.reps FROM sets s2
                   JOIN workouts w2 ON w2.id = s2.workout_id
                   WHERE s2.exercise_id = e.id AND s2.user_id = ?
                     AND s2.weight_kg = mx.pr_kg
                   ORDER BY w2.started_at DESC LIMIT 1
               )), 1) AS est_1rm,
               (SELECT DATE(w2.started_at)
                FROM sets s2 JOIN workouts w2 ON w2.id = s2.workout_id
                WHERE s2.exercise_id = e.id AND s2.user_id = ?
                  AND s2.weight_kg = mx.pr_kg
                ORDER BY w2.started_at DESC LIMIT 1
               ) AS pr_date,
               COUNT(DISTINCT DATE(w.started_at)) AS sessions,
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
        return [dict(r) for r in await cur.fetchall()]
