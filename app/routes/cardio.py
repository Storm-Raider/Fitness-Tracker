import aiosqlite
from fastapi import APIRouter, Depends, Request

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

    return render(
        request,
        "cardio",
        {"logs": logs, "user": dict(current_user)},
    )



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
