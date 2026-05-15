import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import get_db
from app.routes.auth import get_current_user

router = APIRouter()


class RoutineIn(BaseModel):
    name: str
    exercise_ids: list[int]


@router.get("/routines")
async def list_routines(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id, name FROM routines WHERE user_id = ? OR user_id IS NULL ORDER BY name",
        (current_user["id"],),
    ) as cur:
        routines = [dict(r) for r in await cur.fetchall()]

    for r in routines:
        async with conn.execute(
            """
            SELECT e.id, e.name
            FROM routine_exercises re
            JOIN exercises e ON e.id = re.exercise_id
            WHERE re.routine_id = ?
            ORDER BY re.order_idx
            """,
            (r["id"],),
        ) as cur:
            r["exercises"] = [dict(x) for x in await cur.fetchall()]

    return JSONResponse(routines)


@router.post("/routines", status_code=201)
async def create_routine(
    body: RoutineIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Routine name cannot be empty")
    if not body.exercise_ids:
        raise HTTPException(status_code=422, detail="Routine must have at least one exercise")

    async with conn.execute(
        "INSERT INTO routines(name, user_id) VALUES (?, ?)",
        (body.name.strip(), current_user["id"]),
    ) as cur:
        routine_id = cur.lastrowid

    for idx, ex_id in enumerate(body.exercise_ids):
        await conn.execute(
            "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,?)",
            (routine_id, ex_id, idx),
        )

    await conn.commit()
    return JSONResponse({"id": routine_id}, status_code=201)


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(
    routine_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM routines WHERE id = ? AND user_id = ?",
        (routine_id, current_user["id"]),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Routine not found")
    await conn.execute(
        "DELETE FROM routines WHERE id = ? AND user_id = ?",
        (routine_id, current_user["id"]),
    )
    await conn.commit()
