import logging
import os
from datetime import datetime

import aiosqlite
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import get_db
from app.utils.render import render

logger = logging.getLogger(__name__)
router = APIRouter()

_http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient) -> None:
    global _http_client
    _http_client = client


def get_http_client() -> httpx.AsyncClient | None:
    return _http_client


async def _fire_webhook(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
) -> None:
    try:
        resp = await client.post(url, json=payload, timeout=5.0)
        logger.info("Webhook delivered: %s %d", url, resp.status_code)
    except Exception as exc:
        logger.warning("Webhook failed (%s): %s", url, exc)


class WorkoutIn(BaseModel):
    notes: str | None = None


class SetIn(BaseModel):
    exercise_id: int
    reps: int
    weight_kg: float
    notes: str | None = None


@router.get("/workouts")
async def list_workouts(
    request: Request, conn: aiosqlite.Connection = Depends(get_db)
):
    async with conn.execute(
        """
        SELECT id, started_at, ended_at, notes
        FROM workouts
        WHERE user_id = 1
        ORDER BY started_at DESC
        """
    ) as cur:
        workouts = [dict(r) for r in await cur.fetchall()]
    return render(request, "workout_list", {"workouts": workouts})


@router.post("/workouts", status_code=201)
async def create_workout(
    body: WorkoutIn, conn: aiosqlite.Connection = Depends(get_db)
):
    async with conn.execute(
        "INSERT INTO workouts(notes, user_id) VALUES (?, 1)", (body.notes,)
    ) as cur:
        workout_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": workout_id}, status_code=201)


@router.get("/workouts/{workout_id}")
async def get_workout(
    workout_id: int,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    async with conn.execute(
        "SELECT id, started_at, ended_at, notes FROM workouts WHERE id = ? AND user_id = 1",
        (workout_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workout not found")

    async with conn.execute(
        """
        SELECT s.id, e.name AS exercise_name, s.reps, s.weight_kg, s.notes
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.workout_id = ? AND s.user_id = 1
        ORDER BY s.id
        """,
        (workout_id,),
    ) as cur:
        sets = [dict(r) for r in await cur.fetchall()]

    return render(request, "workout_form", {"workout": dict(row), "sets": sets})


@router.post("/workouts/{workout_id}/sets", status_code=201)
async def add_set(
    workout_id: int,
    body: SetIn,
    background_tasks: BackgroundTasks,
    conn: aiosqlite.Connection = Depends(get_db),
):
    # Verify workout exists
    async with conn.execute(
        "SELECT id FROM workouts WHERE id = ? AND user_id = 1", (workout_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Workout not found")

    # Verify exercise exists
    async with conn.execute(
        "SELECT id FROM exercises WHERE id = ?", (body.exercise_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Exercise not found")

    # PR detection + INSERT wrapped in BEGIN IMMEDIATE (prevents async interleaving race)
    async with conn.execute("BEGIN IMMEDIATE"):
        pass
    try:
        async with conn.execute(
            "SELECT MAX(weight_kg) AS max_kg FROM sets WHERE exercise_id = ? AND user_id = 1",
            (body.exercise_id,),
        ) as cur:
            prior_row = await cur.fetchone()
        prior_max = prior_row["max_kg"] if prior_row and prior_row["max_kg"] else None

        async with conn.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (workout_id, body.exercise_id, body.reps, body.weight_kg, body.notes),
        ) as cur:
            set_id = cur.lastrowid

        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("ROLLBACK")
        raise

    is_pr = prior_max is None or body.weight_kg > prior_max
    current_pr = body.weight_kg if is_pr else prior_max

    # Fire webhook asynchronously if this is a PR and WEBHOOK_URL is configured
    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if is_pr and webhook_url and _http_client is not None:
        async with conn.execute(
            "SELECT name FROM exercises WHERE id = ?", (body.exercise_id,)
        ) as cur:
            ex_row = await cur.fetchone()
        payload = {
            "event": "pr_achieved",
            "exercise_name": ex_row["name"] if ex_row else str(body.exercise_id),
            "weight_kg": body.weight_kg,
            "previous_pr_kg": prior_max,
            "workout_id": workout_id,
            "timestamp": datetime.now().isoformat(),
        }
        background_tasks.add_task(_fire_webhook, _http_client, webhook_url, payload)

    return JSONResponse(
        {"id": set_id, "is_pr": is_pr, "current_pr": current_pr}, status_code=201
    )


@router.delete("/workouts/{workout_id}/sets/{set_id}", status_code=204)
async def delete_set(
    workout_id: int,
    set_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
):
    async with conn.execute(
        "SELECT id FROM sets WHERE id = ? AND workout_id = ? AND user_id = 1",
        (set_id, workout_id),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Set not found")

    await conn.execute("DELETE FROM sets WHERE id = ?", (set_id,))
    await conn.commit()
