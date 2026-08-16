import json
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import app.db
from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.db_utils import require_owns
from app.utils.render import templates

router = APIRouter()


class RoutineIn(BaseModel):
    name: str
    exercise_ids: list[int]


class RoutinePatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/routines/manage", response_class=HTMLResponse)
async def routines_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        """
        SELECT r.id, r.name, r.user_id,
               e.id AS ex_id, e.name AS ex_name,
               MIN(re.order_idx) AS ord
        FROM routines r
        LEFT JOIN routine_exercises re ON re.routine_id = r.id
        LEFT JOIN exercises e ON e.id = re.exercise_id
        WHERE r.user_id = ? OR r.user_id IS NULL
        GROUP BY r.id, e.id
        ORDER BY
            CASE WHEN r.user_id = ? THEN 0 ELSE 1 END,
            r.name, ord
        """,
        (uid, uid),
    ) as cur:
        rows = await cur.fetchall()

    routines_map: dict = {}
    for row in rows:
        r_id = row["id"]
        if r_id not in routines_map:
            routines_map[r_id] = {
                "id": r_id,
                "name": row["name"],
                "is_own": row["user_id"] == uid,
                "exercises": [],
            }
        if row["ex_id"] is not None:
            routines_map[r_id]["exercises"].append({
                "id": row["ex_id"],
                "name": row["ex_name"],
            })

    return templates.TemplateResponse(request, "routines.html", {
        "routines": list(routines_map.values()),
        "user": dict(current_user),
    })


@router.patch("/routines/{routine_id}")
async def patch_routine(
    routine_id: int,
    body: RoutinePatch,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await require_owns(conn, "routines", routine_id, uid)
    name = body.name.strip()
    await conn.execute(
        "UPDATE routines SET name = ? WHERE id = ? AND user_id = ?",
        (name, routine_id, uid),
    )
    await conn.commit()
    return JSONResponse({"id": routine_id, "name": name})


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

    # Validate every exercise_id exists BEFORE inserting anything — an invalid
    # id must not create an orphaned zero-exercise routine row.
    placeholders = ",".join("?" * len(body.exercise_ids))
    async with conn.execute(
        f"SELECT id FROM exercises WHERE id IN ({placeholders})",
        body.exercise_ids,
    ) as cur:
        found_ids = {row["id"] for row in await cur.fetchall()}
    missing = [ex_id for ex_id in body.exercise_ids if ex_id not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"Exercise not found: {missing[0]}")

    # Routine insert + routine_exercises inserts wrapped in one transaction so a
    # failure partway through (e.g. an exercise deleted between the validation
    # above and here) rolls back atomically instead of leaving an orphan routine.
    async with app.db.write_lock:
        async with conn.execute("BEGIN IMMEDIATE"):
            pass
        try:
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

            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    return JSONResponse({"id": routine_id}, status_code=201)


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(
    routine_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await require_owns(conn, "routines", routine_id, current_user["id"])
    await conn.execute(
        "DELETE FROM routines WHERE id = ? AND user_id = ?",
        (routine_id, current_user["id"]),
    )
    await conn.commit()
