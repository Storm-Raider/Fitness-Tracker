import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.charts import generate_sparkline
from app.utils.render import render, templates

router = APIRouter()


class ExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/exercises", response_class=HTMLResponse)
async def exercises_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        """
        SELECT e.id, e.name, MAX(s.weight_kg) AS pr_kg
        FROM exercises e
        LEFT JOIN sets s ON s.exercise_id = e.id AND s.user_id = ?
        GROUP BY e.id, e.name
        ORDER BY e.name
        """,
        (current_user["id"],),
    ) as cur:
        exercises = [dict(r) for r in await cur.fetchall()]

    return templates.TemplateResponse(
        request, "exercises.html",
        {"exercises": exercises, "user": dict(current_user)},
    )


@router.get("/api/exercises")
async def list_exercises_json(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        """
        SELECT e.id, e.name, em.muscle, em.is_primary
        FROM exercises e
        LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
        ORDER BY e.name, em.is_primary DESC, em.muscle
        """
    ) as cur:
        rows = await cur.fetchall()

    _ex: dict = {}
    for r in rows:
        if r["id"] not in _ex:
            _ex[r["id"]] = {"id": r["id"], "name": r["name"], "muscles": []}
        if r["muscle"]:
            _ex[r["id"]]["muscles"].append(
                {"name": r["muscle"], "is_primary": bool(r["is_primary"])}
            )
    exercises_list = list(_ex.values())

    async with conn.execute(
        """
        SELECT s.exercise_id, s.weight_kg, s.reps
        FROM sets s
        WHERE s.user_id = ?
          AND s.id IN (
            SELECT MAX(id) FROM sets WHERE user_id = ? GROUP BY exercise_id
          )
        """,
        (current_user["id"], current_user["id"]),
    ) as cur:
        last_sets = {
            str(r["exercise_id"]): {"weight_kg": r["weight_kg"], "reps": r["reps"]}
            for r in await cur.fetchall()
        }

    return JSONResponse({"exercises": exercises_list, "last_sets": last_sets})


@router.post("/exercises", status_code=201)
async def create_exercise(
    body: ExerciseIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        async with conn.execute(
            "INSERT INTO exercises(name) VALUES (?)", (body.name,)
        ) as cur:
            exercise_id = cur.lastrowid
        await conn.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=409, detail="Exercise name already exists")
    return JSONResponse({"id": exercise_id}, status_code=201)


@router.get("/exercises/{exercise_id}", response_class=HTMLResponse)
async def exercise_detail(
    exercise_id: int,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id, name, category, equipment, muscle_primary, muscle_secondary, cue FROM exercises WHERE id = ?",
        (exercise_id,)
    ) as cur:
        ex_row = await cur.fetchone()
    if not ex_row:
        raise HTTPException(status_code=404, detail="Exercise not found")

    uid = current_user["id"]

    async with conn.execute(
        """
        SELECT
            DATE(w.started_at)               AS date,
            MAX(s.weight_kg)                 AS max_kg,
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
            "exercise": dict(ex_row),
            "sessions": [],
            "pr_kg": None,
            "total_sets": 0,
            "total_volume": 0,
            "chart_svg": "",
            "user": dict(current_user),
        })

    pr_kg = max(s["max_kg"] for s in sessions)
    total_sets = sum(s["set_count"] for s in sessions)
    total_volume = round(sum(s["volume_kg"] for s in sessions), 1)

    chrono = list(reversed(sessions))
    chart_svg = generate_sparkline(
        values=[s["max_kg"] for s in chrono],
        labels=[s["date"] for s in chrono],
        color="#f59e0b",
        unit=" kg",
    )

    return templates.TemplateResponse(request, "exercise_detail.html", {
        "exercise": dict(ex_row),
        "sessions": sessions,
        "pr_kg": pr_kg,
        "total_sets": total_sets,
        "total_volume": total_volume,
        "chart_svg": chart_svg,
        "user": dict(current_user),
    })
