import csv
import io
from typing import AsyncGenerator

import aiosqlite
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.db import get_db
from app.routes.auth import get_current_user

router = APIRouter()


async def _csv_rows(conn: aiosqlite.Connection, user_id: int) -> AsyncGenerator[str, None]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "exercise_name", "reps", "weight_kg", "notes"])
    yield buf.getvalue()

    cur = await conn.execute(
        """
        SELECT DATE(w.started_at) AS date,
               e.name             AS exercise_name,
               s.reps,
               s.weight_kg,
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
            writer.writerow([row["date"], row["exercise_name"], row["reps"],
                             row["weight_kg"], row["notes"] or ""])
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
