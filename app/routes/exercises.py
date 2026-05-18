import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
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
        SELECT e.id, e.name, e.category, MAX(s.weight_kg) AS pr_kg
        FROM exercises e
        LEFT JOIN sets s ON s.exercise_id = e.id AND s.user_id = ?
        GROUP BY e.id, e.name, e.category
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
    category: str | None = None,
):
    if category:
        async with conn.execute(
            "SELECT id, name FROM exercises WHERE category = ? ORDER BY name",
            (category,),
        ) as cur:
            rows_cat = await cur.fetchall()
        return JSONResponse({"exercises": [{"id": r["id"], "name": r["name"], "muscles": []} for r in rows_cat]})

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
            "user": dict(current_user),
        })

    pr_kg = max(s["max_kg"] for s in sessions)
    total_sets = sum(s["set_count"] for s in sessions)
    total_volume = round(sum(s["volume_kg"] for s in sessions), 1)

    chrono = list(reversed(sessions))

    return templates.TemplateResponse(request, "exercise_detail.html", {
        "exercise": exercise,
        "sessions": sessions,
        "sessions_json": json.dumps(chrono),
        "pr_kg": pr_kg,
        "total_sets": total_sets,
        "total_volume": total_volume,
        "user": dict(current_user),
    })
