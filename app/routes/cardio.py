import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.db_utils import require_owns
from app.utils.render import render

router = APIRouter()


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
        "SELECT id, name FROM exercises WHERE category = 'Cardio' ORDER BY name",
    ) as cur:
        cardio_exercises = [dict(r) for r in await cur.fetchall()]

    return render(
        request,
        "cardio",
        {"logs": logs, "cardio_exercises": cardio_exercises, "user": dict(current_user)},
    )


@router.post("/cardio", status_code=303)
async def add_standalone_cardio(
    request: Request,
    exercise_id: int = Form(...),
    duration_minutes: float = Form(...),
    distance_km: str = Form(""),
    notes: str = Form(""),
    logged_date: str = Form(""),
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    async with conn.execute(
        "SELECT id FROM exercises WHERE id = ? AND category = 'Cardio'", (exercise_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=400, detail="Invalid cardio exercise")

    dist = float(distance_km) if distance_km.strip() else None
    note = notes.strip() or None
    date = logged_date.strip() or None

    await conn.execute(
        """INSERT INTO cardio_logs(user_id, exercise_id, logged_date, duration_minutes, distance_km, notes)
           VALUES (?, ?, COALESCE(?, date('now','localtime')), ?, ?, ?)""",
        (uid, exercise_id, date, duration_minutes, dist, note),
    )
    await conn.commit()
    return RedirectResponse("/cardio", status_code=303)


@router.delete("/cardio/{log_id}", status_code=200)
async def delete_cardio(
    log_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await require_owns(conn, "cardio_logs", log_id, current_user["id"])
    await conn.execute(
        "DELETE FROM cardio_logs WHERE id = ? AND user_id = ?",
        (log_id, current_user["id"]),
    )
    await conn.commit()
    return ""
