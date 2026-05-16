import json
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
        """
        SELECT r.id, r.name,
               e.id AS ex_id, e.name AS ex_name,
               MIN(re.order_idx) AS ord,
               json_group_array(json_object('name', em.muscle, 'is_primary', em.is_primary))
                   FILTER (WHERE em.muscle IS NOT NULL) AS muscles
        FROM routines r
        LEFT JOIN routine_exercises re ON re.routine_id = r.id
        LEFT JOIN exercises e ON e.id = re.exercise_id
        LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
        WHERE r.user_id = ? OR r.user_id IS NULL
        GROUP BY r.id, e.id
        ORDER BY r.name, ord
        """,
        (current_user["id"],),
    ) as cur:
        rows = await cur.fetchall()

    routines_map: dict = {}
    for row in rows:
        r_id = row["id"]
        if r_id not in routines_map:
            routines_map[r_id] = {"id": r_id, "name": row["name"], "exercises": []}
        if row["ex_id"] is not None:
            routines_map[r_id]["exercises"].append({
                "id": row["ex_id"],
                "name": row["ex_name"],
                "muscles": json.loads(row["muscles"] or "[]"),
            })

    return JSONResponse(list(routines_map.values()))


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
