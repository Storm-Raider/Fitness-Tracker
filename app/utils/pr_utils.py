import aiosqlite


async def fetch_prs(conn: aiosqlite.Connection, uid: int) -> list[dict]:
    """All-time PRs per exercise with est. 1RM, pr_date, sessions, and total_sets."""
    async with conn.execute(
        """
        WITH mx AS (
            SELECT exercise_id, MAX(weight_kg) AS pr_kg
            FROM sets WHERE user_id = ?
            GROUP BY exercise_id
        )
        SELECT e.id,
               e.name,
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
        return [dict(r) for r in await cur.fetchall()]
