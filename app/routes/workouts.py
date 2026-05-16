import logging
import os
from datetime import datetime

import aiosqlite
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render, templates

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
    notes: str | None = Field(default=None, max_length=2000)


class WorkoutPatch(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class SetIn(BaseModel):
    exercise_id: int = Field(gt=0)
    reps: int = Field(ge=1, le=999)
    weight_kg: float = Field(ge=0.0, le=1000.0)
    notes: str | None = Field(default=None, max_length=500)


@router.get("/workouts")
async def list_workouts(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        """
        SELECT w.id, w.started_at, w.ended_at, w.notes,
               COUNT(s.id) AS set_count,
               CASE WHEN w.ended_at IS NOT NULL
                    THEN CAST(ROUND((JULIANDAY(w.ended_at) - JULIANDAY(w.started_at)) * 1440) AS INTEGER)
                    ELSE NULL END AS duration_min
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        WHERE w.user_id = ?
        GROUP BY w.id
        ORDER BY w.started_at DESC
        """,
        (current_user["id"], current_user["id"]),
    ) as cur:
        workouts = [dict(r) for r in await cur.fetchall()]
    return render(request, "workout_list", {"workouts": workouts, "user": dict(current_user)})


@router.post("/workouts", status_code=201)
async def create_workout(
    body: WorkoutIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "INSERT INTO workouts(notes, user_id) VALUES (?, ?)",
        (body.notes, current_user["id"]),
    ) as cur:
        workout_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": workout_id}, status_code=201)


@router.get("/workouts/{workout_id}")
async def get_workout(
    workout_id: int,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id, started_at, ended_at, notes FROM workouts WHERE id = ? AND user_id = ?",
        (workout_id, current_user["id"]),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workout not found")

    async with conn.execute(
        """
        SELECT s.id, s.exercise_id, e.name AS exercise_name, s.reps, s.weight_kg, s.notes
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.workout_id = ? AND s.user_id = ?
        ORDER BY s.id
        """,
        (workout_id, current_user["id"]),
    ) as cur:
        sets = [dict(r) for r in await cur.fetchall()]

    session_volume = sum(s["weight_kg"] * s["reps"] for s in sets)
    return templates.TemplateResponse(request, "workout_form.html", {
        "workout": dict(row),
        "sets": sets,
        "session_volume": session_volume,
        "user": dict(current_user),
    })


@router.patch("/workouts/{workout_id}", status_code=204)
async def patch_workout(
    workout_id: int,
    body: WorkoutPatch,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM workouts WHERE id = ? AND user_id = ?",
        (workout_id, current_user["id"]),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Workout not found")
    await conn.execute(
        "UPDATE workouts SET notes = ? WHERE id = ?", (body.notes, workout_id)
    )
    await conn.commit()


@router.post("/workouts/{workout_id}/sets", status_code=201)
async def add_set(
    workout_id: int,
    body: SetIn,
    background_tasks: BackgroundTasks,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    async with conn.execute(
        "SELECT id FROM workouts WHERE id = ? AND user_id = ?", (workout_id, uid)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Workout not found")

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
            "SELECT MAX(weight_kg) AS max_kg FROM sets WHERE exercise_id = ? AND user_id = ?",
            (body.exercise_id, uid),
        ) as cur:
            prior_row = await cur.fetchone()
        prior_max = prior_row["max_kg"] if prior_row and prior_row["max_kg"] else None

        async with conn.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (workout_id, body.exercise_id, body.reps, body.weight_kg, body.notes, uid),
        ) as cur:
            set_id = cur.lastrowid

        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("ROLLBACK")
        raise

    is_pr = prior_max is None or body.weight_kg > prior_max
    current_pr = body.weight_kg if is_pr else prior_max

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if is_pr and webhook_url and _http_client is not None:
        async with conn.execute(
            "SELECT name FROM exercises WHERE id = ?", (body.exercise_id,)
        ) as cur:
            ex_row = await cur.fetchone()
        payload = {
            "event": "pr_achieved",
            "user_id": uid,
            "username": current_user["username"],
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


@router.post("/workouts/{workout_id}/finish")
async def finish_workout(
    workout_id: int,
    background_tasks: BackgroundTasks,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id, started_at, ended_at FROM workouts WHERE id = ? AND user_id = ?",
        (workout_id, uid),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workout not found")

    if not row["ended_at"]:
        await conn.execute(
            "UPDATE workouts SET ended_at = datetime('now','localtime') WHERE id = ?",
            (workout_id,),
        )
        await conn.commit()

    async with conn.execute(
        "SELECT started_at, ended_at FROM workouts WHERE id = ?", (workout_id,)
    ) as cur:
        w = dict(await cur.fetchone())

    async with conn.execute(
        """
        SELECT COUNT(*)                          AS set_count,
               COALESCE(SUM(weight_kg * reps),0) AS volume_kg
        FROM sets WHERE workout_id = ? AND user_id = ?
        """,
        (workout_id, uid),
    ) as cur:
        stats = dict(await cur.fetchone())

    started = datetime.fromisoformat(w["started_at"])
    ended   = datetime.fromisoformat(w["ended_at"])
    duration_minutes = round((ended - started).total_seconds() / 60, 1)

    summary = {
        "workout_id":       workout_id,
        "duration_minutes": duration_minutes,
        "set_count":        stats["set_count"],
        "volume_kg":        round(stats["volume_kg"], 1),
        "timestamp":        w["ended_at"],
    }

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if webhook_url and _http_client is not None:
        background_tasks.add_task(
            _fire_webhook, _http_client, webhook_url,
            {"event": "session_complete", "user_id": uid, "username": current_user["username"], **summary},
        )

    return JSONResponse(summary)


@router.delete("/workouts/{workout_id}", status_code=200)
async def delete_workout(
    workout_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id FROM workouts WHERE id = ? AND user_id = ?", (workout_id, uid)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Workout not found")
    await conn.execute("DELETE FROM sets WHERE workout_id = ? AND user_id = ?", (workout_id, uid))
    await conn.execute("DELETE FROM workouts WHERE id = ? AND user_id = ?", (workout_id, uid))
    await conn.commit()
    return ""


@router.delete("/workouts/{workout_id}/sets/{set_id}", status_code=204)
async def delete_set(
    workout_id: int,
    set_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id FROM sets WHERE id = ? AND workout_id = ? AND user_id = ?",
        (set_id, workout_id, uid),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Set not found")

    # user_id included in DELETE to close the IDOR window (two-transaction race)
    await conn.execute(
        "DELETE FROM sets WHERE id = ? AND user_id = ?", (set_id, uid)
    )
    await conn.commit()
