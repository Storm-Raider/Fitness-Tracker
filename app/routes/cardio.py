import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render

router = APIRouter()


class CardioIn(BaseModel):
    exercise_id: int
    logged_date: str
    duration_minutes: float = Field(gt=0, le=1440)
    distance_km: float | None = Field(default=None, ge=0, le=10_000)
    notes: str | None = None


@router.get("/cardio")
async def cardio_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        """
        SELECT cl.id, cl.logged_date, cl.duration_minutes, cl.distance_km, cl.notes,
               e.name AS exercise_name
        FROM cardio_logs cl
        JOIN exercises e ON e.id = cl.exercise_id
        WHERE cl.user_id = ?
        ORDER BY cl.logged_date DESC, cl.created_at DESC
        """,
        (current_user["id"],),
    ) as cur:
        logs = [dict(r) for r in await cur.fetchall()]

    async with conn.execute(
        "SELECT id, name FROM exercises WHERE category = 'Cardio' ORDER BY name"
    ) as cur:
        cardio_exercises = [dict(r) for r in await cur.fetchall()]

    return render(
        request,
        "cardio",
        {"logs": logs, "exercises": cardio_exercises, "user": dict(current_user)},
    )


@router.post("/cardio", status_code=201)
async def log_cardio(
    body: CardioIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM exercises WHERE id = ? AND category = 'Cardio'",
        (body.exercise_id,),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=400, detail="Invalid cardio exercise")

    async with conn.execute(
        """INSERT INTO cardio_logs(user_id, exercise_id, logged_date, duration_minutes, distance_km, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            current_user["id"],
            body.exercise_id,
            body.logged_date,
            body.duration_minutes,
            body.distance_km,
            body.notes or None,
        ),
    ) as cur:
        log_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": log_id}, status_code=201)


@router.delete("/cardio/{log_id}", status_code=200)
async def delete_cardio(
    log_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM cardio_logs WHERE id = ? AND user_id = ?",
        (log_id, current_user["id"]),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Entry not found")
    await conn.execute(
        "DELETE FROM cardio_logs WHERE id = ? AND user_id = ?",
        (log_id, current_user["id"]),
    )
    await conn.commit()
    return ""
