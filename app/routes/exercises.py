import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.data.exercises import infer_muscle_and_category
from app.db import get_db, write_tx
from app.routes.auth import get_current_user
from app.utils.coach_plan import invalidate_exercise_caches, validate_exercise_name
from app.utils.pr_utils import bodyweight_bests, bw_key
from app.utils.render import templates

router = APIRouter()

_MUSCLES = {"Chest", "Back", "Legs", "Shoulders", "Biceps", "Triceps", "Abs", "Forearms"}


class ExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # Optional: the muscle group this exercise targets. When omitted, it's
    # inferred from the name so a custom exercise never lands muscle-less.
    muscle_primary: str | None = Field(default=None, max_length=20)


class GoalIn(BaseModel):
    target_kg: float = Field(gt=0, le=1000)


_MUSCLE_ORDER = ["Chest", "Back", "Legs", "Shoulders", "Biceps", "Triceps", "Abs", "Forearms"]


@router.get("/exercises", response_class=HTMLResponse)
async def exercises_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        """
        SELECT e.id, e.name, e.category, MAX(s.weight_kg) AS pr_kg,
               GROUP_CONCAT(DISTINCT CASE WHEN em.is_primary = 1 THEN em.muscle END) AS primary_muscles
        FROM exercises e
        LEFT JOIN sets s ON s.exercise_id = e.id AND s.user_id = ?
        LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
        GROUP BY e.id, e.name, e.category
        ORDER BY e.name
        """,
        (current_user["id"],),
    ) as cur:
        rows = await cur.fetchall()

    # Bodyweight exercises show their best set, not bodyweight as a "PR".
    bw_best = await bodyweight_bests(conn, current_user["id"])

    by_muscle: dict[str, list] = {m: [] for m in _MUSCLE_ORDER}
    exercises = []
    other = []
    for row in rows:
        ex = dict(row)
        ex["bw"] = bw_best.get(ex["id"])
        muscles = [m.strip() for m in (ex.pop("primary_muscles") or "").split(",") if m.strip()]
        ex["muscles"] = muscles
        exercises.append(ex)
        matched = False
        for m in muscles:
            if m in by_muscle:
                by_muscle[m].append(ex)
                matched = True
        if not matched:
            other.append(ex)

    by_muscle = {m: exs for m, exs in by_muscle.items() if exs}
    # Exercises with no recognized muscle tag would otherwise vanish from the
    # browse page entirely (though still counted in the header total and
    # reachable via /exercises/{id}). Give them a catch-all group, kept last
    # so it doesn't compete visually with real muscle groups.
    if other:
        by_muscle["Other"] = other

    async with conn.execute(
        """
        SELECT e.id, e.name, e.category, MAX(s.weight_kg) AS pr_kg,
               MAX(w.started_at) AS last_used
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
        GROUP BY e.id, e.name, e.category
        ORDER BY last_used DESC
        LIMIT 6
        """,
        (current_user["id"],),
    ) as cur:
        recent_exercises = [dict(r) for r in await cur.fetchall()]
    for ex in recent_exercises:
        ex["bw"] = bw_best.get(ex["id"])

    return templates.TemplateResponse(
        request, "exercises.html",
        {
            "exercises": exercises,
            "by_muscle": by_muscle,
            "recent_exercises": recent_exercises,
            "user": dict(current_user),
        },
    )


@router.get("/api/exercises")
async def list_exercises_json(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
    category: str | None = None,
    exclude_workout: int | None = None,
):
    # exclude_workout: the logger asks for "last time" from before the session
    # it is showing, so a reload mid-session doesn't echo today's sets back.
    if category:
        async with conn.execute(
            "SELECT id, name FROM exercises WHERE category = ? ORDER BY name",
            (category,),
        ) as cur:
            rows_cat = await cur.fetchall()
        return JSONResponse({"exercises": [{"id": r["id"], "name": r["name"], "muscles": []} for r in rows_cat]})

    async with conn.execute(
        """
        SELECT e.id, e.name, e.equipment, e.log_type, em.muscle, em.is_primary
        FROM exercises e
        LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
        ORDER BY e.name, em.is_primary DESC, em.muscle
        """
    ) as cur:
        rows = await cur.fetchall()

    _ex: dict = {}
    for r in rows:
        if r["id"] not in _ex:
            _ex[r["id"]] = {"id": r["id"], "name": r["name"],
                            "is_bodyweight": r["equipment"] == "Bodyweight",
                            "is_time": r["log_type"] == "time", "muscles": []}
        if r["muscle"]:
            _ex[r["id"]]["muscles"].append(
                {"name": r["muscle"], "is_primary": bool(r["is_primary"])}
            )
    exercises_list = list(_ex.values())

    async with conn.execute(
        """
        SELECT s.exercise_id, s.weight_kg, s.reps, s.added_weight_kg, s.duration_seconds
        FROM sets s
        WHERE s.user_id = ?
          AND s.id IN (
            SELECT MAX(id) FROM sets
            WHERE user_id = ? AND (? IS NULL OR workout_id != ?)
            GROUP BY exercise_id
          )
        """,
        (current_user["id"], current_user["id"], exclude_workout, exclude_workout),
    ) as cur:
        last_sets = {
            str(r["exercise_id"]): {"weight_kg": r["weight_kg"], "reps": r["reps"],
                                    "added_weight_kg": r["added_weight_kg"],
                                    "duration_seconds": r["duration_seconds"]}
            for r in await cur.fetchall()
        }

    return JSONResponse({"exercises": exercises_list, "last_sets": last_sets})


@router.post("/exercises", status_code=201)
async def create_exercise(
    body: ExerciseIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Exercise names end up inside AI prompts, so they are restricted to a safe alphabet.
    try:
        name = validate_exercise_name(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # Resolve the muscle group + category: use the one the user picked if valid,
    # otherwise infer from the name so the exercise is never left unclassified.
    inferred_muscle, inferred_cat = infer_muscle_and_category(name)
    muscle = body.muscle_primary if body.muscle_primary in _MUSCLES else inferred_muscle
    category = inferred_cat

    try:
        async with write_tx(conn):
            async with conn.execute(
                "INSERT INTO exercises(name, category) VALUES (?, ?)", (name, category or None)
            ) as cur:
                exercise_id = cur.lastrowid
            if muscle:
                await conn.execute(
                    "INSERT INTO exercise_muscles(exercise_id, muscle, is_primary) VALUES (?, ?, 1)",
                    (exercise_id, muscle),
                )
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="Exercise name already exists")
    invalidate_exercise_caches()   # the coach's catalog and name matcher are process-lifetime caches
    return JSONResponse({"id": exercise_id, "muscle": muscle, "category": category}, status_code=201)


@router.get("/exercises/{exercise_id}", response_class=HTMLResponse)
async def exercise_detail(
    exercise_id: int,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        """
        SELECT e.id, e.name, e.category, e.equipment, e.cue,
               em.muscle, em.is_primary
        FROM exercises e
        LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
        WHERE e.id = ?
        ORDER BY em.is_primary DESC, em.muscle
        """,
        (exercise_id,),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Exercise not found")

    first = rows[0]
    exercise = {
        "id": first["id"],
        "name": first["name"],
        "category": first["category"],
        "equipment": first["equipment"],
        "cue": first["cue"],
        "muscles": [
            {"name": r["muscle"], "is_primary": bool(r["is_primary"])}
            for r in rows if r["muscle"]
        ],
    }

    uid = current_user["id"]

    async with conn.execute(
        "SELECT target_kg FROM exercise_goals WHERE user_id = ? AND exercise_id = ?",
        (uid, exercise_id),
    ) as cur:
        _goal = await cur.fetchone()
    exercise_goal_kg = _goal["target_kg"] if _goal else None

    async with conn.execute(
        """
        SELECT
            DATE(w.started_at)               AS date,
            MAX(s.weight_kg)                 AS max_kg,
            (
                SELECT s2.reps FROM sets s2
                JOIN workouts w2 ON w2.id = s2.workout_id
                WHERE s2.exercise_id = s.exercise_id
                  AND s2.user_id = s.user_id
                  AND DATE(w2.started_at) = DATE(w.started_at)
                ORDER BY s2.weight_kg DESC LIMIT 1
            )                                AS max_reps,
            COUNT(s.id)                      AS set_count,
            ROUND(SUM(s.weight_kg * s.reps), 1) AS volume_kg
        FROM sets s
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.exercise_id = ? AND s.user_id = ?
        GROUP BY DATE(w.started_at)
        ORDER BY date DESC
        LIMIT 52
        """,
        (exercise_id, uid),
    ) as cur:
        sessions = [dict(r) for r in await cur.fetchall()]

    if not sessions:
        return templates.TemplateResponse(request, "exercise_detail.html", {
            "exercise": exercise,
            "sessions": [],
            "sessions_json": "[]",
            "pr_kg": None,
            "total_sets": 0,
            "total_volume": 0,
            "exercise_goal_kg": exercise_goal_kg,
            "is_bw": exercise["equipment"] == "Bodyweight",
            "user": dict(current_user),
        })

    pr_kg = max(s["max_kg"] for s in sessions)
    total_sets = sum(s["set_count"] for s in sessions)
    total_volume = round(sum(s["volume_kg"] for s in sessions), 1)

    # Bodyweight exercises: weight_kg is the lifter's bodyweight, so the page
    # charts and ranks the best set (added load, hold, reps) and total reps.
    is_bw = exercise["equipment"] == "Bodyweight"
    bw = None
    if is_bw:
        async with conn.execute(
            """
            SELECT DATE(w.started_at) AS day, COALESCE(s.added_weight_kg, 0) AS added,
                   s.reps, s.duration_seconds AS secs
            FROM sets s JOIN workouts w ON w.id = s.workout_id
            WHERE s.exercise_id = ? AND s.user_id = ?
            """,
            (exercise_id, uid),
        ) as cur:
            bw_rows = await cur.fetchall()
        per_day: dict[str, dict] = {}
        for r in bw_rows:
            d = per_day.setdefault(r["day"], {"best": None, "total_reps": 0})
            d["total_reps"] += r["reps"] or 0
            if d["best"] is None or bw_key(r["added"], r["reps"], r["secs"]) > bw_key(*d["best"]):
                d["best"] = (r["added"], r["reps"], r["secs"])
        for s in sessions:
            d = per_day.get(s["date"])
            added, reps, secs = d["best"] if d else (0, 0, None)
            s.update(bw_added=added, bw_reps=reps, bw_secs=secs,
                     total_reps=d["total_reps"] if d else 0)
        bw = (await bodyweight_bests(conn, uid, exercise_id)).get(exercise_id)
        best_key = bw_key(bw["added"], bw["reps"], bw["secs"]) if bw else None
        for s in sessions:
            s["is_best"] = best_key is not None and bw_key(s["bw_added"], s["bw_reps"], s["bw_secs"]) == best_key

    chrono = list(reversed(sessions))

    return templates.TemplateResponse(request, "exercise_detail.html", {
        "exercise": exercise,
        "sessions": sessions,
        "sessions_json": json.dumps(chrono),
        "pr_kg": pr_kg,
        "total_sets": total_sets,
        "total_volume": total_volume,
        "exercise_goal_kg": exercise_goal_kg,
        "user": dict(current_user),
        "is_bw": is_bw,
        "bw": bw,
    })


@router.put("/exercises/{exercise_id}/goal", status_code=200)
async def set_exercise_goal(
    exercise_id: int,
    body: GoalIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute("SELECT 1 FROM exercises WHERE id = ?", (exercise_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Exercise not found")   # not a FK 500
    await conn.execute(
        "INSERT INTO exercise_goals(user_id, exercise_id, target_kg) VALUES(?,?,?) "
        "ON CONFLICT(user_id, exercise_id) DO UPDATE SET target_kg=excluded.target_kg",
        (current_user["id"], exercise_id, body.target_kg),
    )
    await conn.commit()
    return JSONResponse({"target_kg": body.target_kg})


@router.delete("/exercises/{exercise_id}/goal", status_code=204)
async def delete_exercise_goal(
    exercise_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await conn.execute(
        "DELETE FROM exercise_goals WHERE user_id = ? AND exercise_id = ?",
        (current_user["id"], exercise_id),
    )
    await conn.commit()
