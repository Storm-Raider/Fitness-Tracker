import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import get_db

router = APIRouter()


class ExerciseIn(BaseModel):
    name: str


@router.get("/exercises")
async def list_exercises(conn: aiosqlite.Connection = Depends(get_db)):
    async with conn.execute("SELECT id, name FROM exercises ORDER BY name") as cur:
        exercises = [dict(r) for r in await cur.fetchall()]

    # Pre-load last-session context: most recent set per exercise (offline-capable in workout form)
    async with conn.execute(
        """
        SELECT s.exercise_id, s.weight_kg, s.reps
        FROM sets s
        WHERE s.user_id = 1
          AND s.id IN (
            SELECT MAX(id) FROM sets WHERE user_id = 1 GROUP BY exercise_id
          )
        """
    ) as cur:
        last_sets = {
            str(r["exercise_id"]): {"weight_kg": r["weight_kg"], "reps": r["reps"]}
            for r in await cur.fetchall()
        }

    return JSONResponse({"exercises": exercises, "last_sets": last_sets})


@router.post("/exercises", status_code=201)
async def create_exercise(
    body: ExerciseIn, conn: aiosqlite.Connection = Depends(get_db)
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
