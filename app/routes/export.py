import csv
import io
from typing import AsyncGenerator

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render

router = APIRouter()


@router.get("/export", response_class=HTMLResponse)
async def export_page(
    request: Request,
    current_user=Depends(get_current_user),
):
    return render(request, "export", {"user": dict(current_user)})


async def _csv_rows(conn: aiosqlite.Connection, user_id: int) -> AsyncGenerator[str, None]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "workout_id", "exercise_name", "reps", "weight_kg", "rpe", "notes"])
    yield buf.getvalue()

    cur = await conn.execute(
        """
        SELECT DATE(w.started_at) AS date,
               w.id               AS workout_id,
               e.name             AS exercise_name,
               s.reps,
               s.weight_kg,
               s.rpe,
               s.notes
        FROM sets s
        JOIN workouts w  ON w.id = s.workout_id
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ?
        ORDER BY w.started_at, s.id
        """,
        (user_id,),
    )
    try:
        async for row in cur:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([row["date"], row["workout_id"], row["exercise_name"],
                             row["reps"], row["weight_kg"], row["rpe"] or "",
                             row["notes"] or ""])
            yield buf.getvalue()
    finally:
        await cur.close()


@router.get("/export/workouts.csv")
async def export_csv(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return StreamingResponse(
        _csv_rows(conn, current_user["id"]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=workouts.csv"},
    )
