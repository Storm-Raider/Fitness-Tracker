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
        SELECT e.id, e.name, e.category, e.tier, MAX(s.weight_kg) AS pr_kg,
               COUNT(DISTINCT s.id) AS uses,
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
        # Common = the bundled database's tier 1, anything you have logged, and
        # exercises you made yourself (no tier). The rest folds under "N more".
        ex["common"] = (ex.get("tier") or 1) == 1 or (ex.get("uses") or 0) > 0
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


# How many exercises a phone keeps pictures and steps for (about 20 KB each).
OFFLINE_PACK_LIMIT = 250


@router.get("/api/exercises/offline-pack")
async def exercise_offline_pack(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The exercises this athlete is likely to open mid-session, so the phone can
    save their pictures and How-to steps while it has signal (static/sw.js keeps
    them across app updates). Their own first: routines, the latest plans and
    anything logged in the last 90 days, most-logged first; then the common
    tier, until OFFLINE_PACK_LIMIT."""
    uid = current_user["id"]
    ordered: list[int] = []
    seen: set[int] = set()

    def add(ids):
        for i in ids:
            if i not in seen and len(ordered) < OFFLINE_PACK_LIMIT:
                seen.add(i)
                ordered.append(i)

    async with conn.execute(
        """SELECT re.exercise_id FROM routine_exercises re JOIN routines r ON r.id = re.routine_id
           WHERE r.user_id = ? ORDER BY r.id, re.order_idx""", (uid,),
    ) as cur:
        add(r[0] for r in await cur.fetchall())

    async with conn.execute(
        "SELECT plan_json FROM coach_plans WHERE user_id = ? ORDER BY id DESC LIMIT 2", (uid,),
    ) as cur:
        plans = [r[0] for r in await cur.fetchall()]
    names: list[str] = []
    for raw in plans:
        try:
            plan = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for day in plan.get("days") or []:
            names += [e.get("name") for e in day.get("exercises") or [] if e.get("name")]
    if names:
        marks = ",".join("?" * len(names))
        async with conn.execute(f"SELECT id, name FROM exercises WHERE name IN ({marks})", names) as cur:
            id_of = {r["name"]: r["id"] for r in await cur.fetchall()}
        add(id_of[n] for n in names if n in id_of)

    async with conn.execute(
        """SELECT s.exercise_id FROM sets s JOIN workouts w ON w.id = s.workout_id
           WHERE s.user_id = ? AND w.started_at >= datetime('now', 'localtime', '-90 days')
           GROUP BY s.exercise_id ORDER BY COUNT(*) DESC""", (uid,),
    ) as cur:
        add(r[0] for r in await cur.fetchall())

    async with conn.execute("SELECT id FROM exercises WHERE tier = 1 ORDER BY name") as cur:
        add(r[0] for r in await cur.fetchall())

    if not ordered:
        return JSONResponse({"exercises": [], "urls": []})
    marks = ",".join("?" * len(ordered))
    async with conn.execute(
        f"""SELECT id, images, COALESCE(json_array_length(instructions), 0) AS n_steps
            FROM exercises WHERE id IN ({marks})""", ordered,
    ) as cur:
        info = {r["id"]: r for r in await cur.fetchall()}
    urls: list[str] = []
    kept: list[int] = []
    for i in ordered:
        r = info.get(i)
        if r is None:
            continue
        images = json.loads(r["images"] or "[]")
        if not images and not r["n_steps"]:
            continue                       # nothing to show offline
        kept.append(i)
        urls.append(f"/api/exercises/{i}/howto")
        urls += images
    return JSONResponse({"exercises": kept, "urls": urls},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/exercises/{exercise_id}/howto")
async def exercise_howto(
    exercise_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """What the in-session "How to" sheet shows: the detail page's steps and
    photos, without leaving the log."""
    async with conn.execute(
        "SELECT id, name, cue, level, instructions, images, steps_source FROM exercises WHERE id = ?",
        (exercise_id,),
    ) as cur:
        r = await cur.fetchone()
    if r is None:
        return JSONResponse({"detail": "Exercise not found"}, status_code=404)
    images = json.loads(r["images"] or "[]")
    return JSONResponse({
        "id": r["id"], "name": r["name"], "cue": r["cue"], "level": r["level"],
        "instructions": json.loads(r["instructions"] or "[]"),
        "images": images,
        "steps_source": r["steps_source"],
        "illustrated": bool(images) and "/repdb/" in images[0],
    })


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
        SELECT e.id, e.name, e.equipment, e.log_type, e.tier, em.muscle, em.is_primary,
               json_extract(e.images, '$[0]') AS thumb,
               COALESCE(json_array_length(e.instructions), 0) AS n_steps,
               (SELECT COUNT(*) FROM sets s WHERE s.exercise_id = e.id AND s.user_id = :uid) AS uses
        FROM exercises e
        LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
        ORDER BY e.name, em.is_primary DESC, em.muscle
        """,
        {"uid": current_user["id"]},
    ) as cur:
        rows = await cur.fetchall()

    _ex: dict = {}
    for r in rows:
        if r["id"] not in _ex:
            _ex[r["id"]] = {"id": r["id"], "name": r["name"],
                            "is_bodyweight": r["equipment"] == "Bodyweight",
                            "is_time": r["log_type"] == "time", "muscles": [],
                            "tier": r["tier"] or 1, "uses": r["uses"] or 0,
                            "thumb": r["thumb"],
                            "has_howto": bool(r["n_steps"] or r["thumb"])}
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

    # Best estimated 1RM per exercise from before this session (Epley, 1-12 reps,
    # where the estimate holds up). Shown beside "Last time" on the session screen.
    async with conn.execute(
        """
        SELECT exercise_id, MAX(e1rm(weight_kg, reps)) AS best
        FROM sets
        WHERE user_id = ? AND weight_kg > 0 AND reps BETWEEN 1 AND 12
          AND (? IS NULL OR workout_id != ?)
        GROUP BY exercise_id
        """,
        (current_user["id"], exclude_workout, exclude_workout),
    ) as cur:
        best_e1rm = {str(r["exercise_id"]): round(r["best"], 1) for r in await cur.fetchall() if r["best"]}

    return JSONResponse({"exercises": exercises_list, "last_sets": last_sets, "best_e1rm": best_e1rm})


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
        SELECT e.id, e.name, e.category, e.equipment, e.cue, e.level, e.instructions, e.steps_source, e.images,
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
        "level": first["level"],
        "instructions": json.loads(first["instructions"] or "[]"),
        "steps_source": first["steps_source"],
        "images": json.loads(first["images"] or "[]"),
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
