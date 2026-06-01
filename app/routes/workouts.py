import logging
import os
from datetime import datetime

import aiosqlite
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import trash
from app.utils.db_utils import require_owns
from app.utils.render import render, templates


def _undo_response(token: str, label: str, status_code: int = 200) -> Response:
    """Empty-body response carrying undo metadata in headers, so HTMX swaps
    still work and JS/HTMX delete handlers can surface an undo toast."""
    from urllib.parse import quote
    return Response(
        status_code=status_code,
        headers={"X-Undo-Token": token, "X-Undo-Label": quote(label)},
    )

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
    rpe: int | None = Field(default=None, ge=1, le=10)


class SessionCardioIn(BaseModel):
    exercise_id: int = Field(gt=0)
    duration_minutes: float = Field(gt=0, le=1440)
    distance_km: float | None = Field(default=None, ge=0, le=10_000)
    notes: str | None = Field(default=None, max_length=500)


class SetPatch(BaseModel):
    reps: int = Field(ge=1, le=999)
    weight_kg: float = Field(ge=0.0, le=1000.0)
    notes: str | None = Field(default=None, max_length=500)
    rpe: int | None = Field(default=None, ge=1, le=10)


@router.get("/workouts")
async def list_workouts(
    request: Request,
    q: str | None = None,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    # Cleanup ghost sessions: unfinished workouts with no sets older than 1 hour
    await conn.execute(
        """DELETE FROM workouts
           WHERE user_id = ?
             AND ended_at IS NULL
             AND NOT EXISTS (SELECT 1 FROM sets WHERE workout_id = workouts.id)
             AND started_at < datetime('now', 'localtime', '-1 hour')""",
        (uid,),
    )
    await conn.commit()

    search = q.strip() if q else ""
    like = f"%{search}%" if search else None

    async with conn.execute(
        """
        SELECT w.id, w.started_at, w.ended_at, w.notes,
               COUNT(s.id) AS set_count,
               COALESCE(ROUND(SUM(s.weight_kg * s.reps), 0), 0) AS volume_kg,
               CASE WHEN w.ended_at IS NOT NULL
                    THEN CAST(ROUND((JULIANDAY(w.ended_at) - JULIANDAY(w.started_at)) * 1440) AS INTEGER)
                    ELSE NULL END AS duration_min,
               CASE WHEN w.ended_at IS NULL
                    THEN CAST(ROUND((JULIANDAY('now','localtime') - JULIANDAY(w.started_at)) * 1440) AS INTEGER)
                    ELSE NULL END AS elapsed_min,
               (SELECT GROUP_CONCAT(ep.name, ', ')
                FROM (SELECT DISTINCT e.name
                      FROM sets ep_s
                      JOIN exercises e ON e.id = ep_s.exercise_id
                      WHERE ep_s.workout_id = w.id
                      ORDER BY ep_s.id LIMIT 3) ep
               ) AS exercise_preview
        FROM workouts w
        LEFT JOIN sets s ON s.workout_id = w.id AND s.user_id = ?
        WHERE w.user_id = ?
          AND (? IS NULL
               OR DATE(w.started_at, 'localtime') LIKE ?
               OR LOWER(COALESCE(w.notes, '')) LIKE LOWER(?)
               OR EXISTS (
                   SELECT 1 FROM sets sq
                   JOIN exercises eq ON eq.id = sq.exercise_id
                   WHERE sq.workout_id = w.id
                     AND LOWER(eq.name) LIKE LOWER(?)
               ))
        GROUP BY w.id
        HAVING w.ended_at IS NULL OR COUNT(s.id) > 0
        ORDER BY w.started_at DESC
        """,
        (uid, uid, like, like, like, like),
    ) as cur:
        workouts = [dict(r) for r in await cur.fetchall()]

    active_workout = next((w for w in workouts if w["ended_at"] is None), None)
    return render(request, "workout_list", {
        "workouts": workouts,
        "active_workout": active_workout,
        "user": dict(current_user),
        "q": search,
    })


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
        SELECT s.id, s.exercise_id, e.name AS exercise_name, s.reps, s.weight_kg, s.notes, s.rpe
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.workout_id = ? AND s.user_id = ?
        ORDER BY s.id
        """,
        (workout_id, current_user["id"]),
    ) as cur:
        sets = [dict(r) for r in await cur.fetchall()]

    async with conn.execute(
        """
        SELECT cl.id, cl.exercise_id, e.name AS exercise_name,
               cl.duration_minutes, cl.distance_km, cl.notes
        FROM cardio_logs cl
        JOIN exercises e ON e.id = cl.exercise_id
        WHERE cl.workout_id = ? AND cl.user_id = ?
        ORDER BY cl.id
        """,
        (workout_id, current_user["id"]),
    ) as cur:
        cardio_logs = [dict(r) for r in await cur.fetchall()]

    session_volume = sum(s["weight_kg"] * s["reps"] for s in sets)
    is_finished = row["ended_at"] is not None
    duration_min = None
    if is_finished:
        started = datetime.fromisoformat(row["started_at"])
        ended = datetime.fromisoformat(row["ended_at"])
        duration_min = round((ended - started).total_seconds() / 60)

    template_exercises = []
    tpl_id = request.query_params.get("tpl")
    if tpl_id and not is_finished:
        async with conn.execute(
            """SELECT e.id, e.name FROM workout_template_exercises wte
               JOIN exercises e ON e.id = wte.exercise_id
               WHERE wte.template_id = ? ORDER BY wte.order_idx""",
            (tpl_id,),
        ) as cur:
            template_exercises = [dict(r) for r in await cur.fetchall()]

    # Form info enrichment (generated nightly by scripts/enrich_workouts.py)
    enrichment = None
    if is_finished:
        import json as _json
        async with conn.execute(
            "SELECT form_info FROM workout_enrichments WHERE workout_id = ?",
            (workout_id,),
        ) as cur:
            enc_row = await cur.fetchone()
        if enc_row:
            try:
                enrichment = _json.loads(enc_row["form_info"])
            except Exception:
                enrichment = None

    return templates.TemplateResponse(request, "workout_form.html", {
        "workout": dict(row),
        "sets": sets,
        "cardio_logs": cardio_logs,
        "session_volume": session_volume,
        "user": dict(current_user),
        "is_finished": is_finished,
        "duration_min": duration_min,
        "template_exercises": template_exercises,
        "enrichment": enrichment,
    })


@router.patch("/workouts/{workout_id}", status_code=204)
async def patch_workout(
    workout_id: int,
    body: WorkoutPatch,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await require_owns(conn, "workouts", workout_id, current_user["id"])
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

    await require_owns(conn, "workouts", workout_id, uid)

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
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, rpe, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workout_id, body.exercise_id, body.reps, body.weight_kg, body.notes, body.rpe, uid),
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


@router.get("/api/workouts/{workout_id}/exercises")
async def get_workout_exercise_order(
    workout_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await require_owns(conn, "workouts", workout_id, uid)
    async with conn.execute(
        """
        SELECT s.exercise_id AS id, e.name, MIN(s.id) AS first_id
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.workout_id = ? AND s.user_id = ?
        GROUP BY s.exercise_id
        ORDER BY first_id
        """,
        (workout_id, uid),
    ) as cur:
        exercises = [{"id": r["id"], "name": r["name"]} for r in await cur.fetchall()]
    return JSONResponse(exercises)


@router.delete("/workouts/{workout_id}", status_code=200)
async def delete_workout(
    workout_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await require_owns(conn, "workouts", workout_id, uid)
    token, label = await trash.soft_delete_workout(conn, uid, workout_id)
    await conn.commit()
    return _undo_response(token, label)


@router.post("/workouts/{workout_id}/cardio", status_code=201)
async def add_session_cardio(
    workout_id: int,
    body: SessionCardioIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await require_owns(conn, "workouts", workout_id, uid)
    async with conn.execute(
        "SELECT id FROM exercises WHERE id = ? AND category = 'Cardio'", (body.exercise_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=400, detail="Invalid cardio exercise")
    async with conn.execute(
        """INSERT INTO cardio_logs(user_id, exercise_id, workout_id, logged_date, duration_minutes, distance_km, notes)
           VALUES (?, ?, ?, date('now','localtime'), ?, ?, ?)""",
        (uid, body.exercise_id, workout_id, body.duration_minutes, body.distance_km, body.notes),
    ) as cur:
        log_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": log_id}, status_code=201)


@router.delete("/workouts/{workout_id}/cardio/{log_id}", status_code=200)
async def delete_session_cardio(
    workout_id: int,
    log_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id FROM cardio_logs WHERE id = ? AND workout_id = ? AND user_id = ?",
        (log_id, workout_id, uid),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Cardio entry not found")
    token, label = await trash.soft_delete_cardio(conn, uid, log_id)
    await conn.commit()
    return _undo_response(token, label)


@router.patch("/workouts/{workout_id}/sets/{set_id}")
async def patch_set(
    workout_id: int,
    set_id: int,
    body: SetPatch,
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
    await conn.execute(
        "UPDATE sets SET reps = ?, weight_kg = ?, notes = ?, rpe = ? WHERE id = ? AND user_id = ?",
        (body.reps, body.weight_kg, body.notes, body.rpe, set_id, uid),
    )
    await conn.commit()
    return JSONResponse({
        "id": set_id, "reps": body.reps, "weight_kg": body.weight_kg,
        "notes": body.notes, "rpe": body.rpe,
    })


@router.delete("/workouts/{workout_id}/sets/{set_id}", status_code=200)
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

    # soft_delete_set scopes the DELETE by user_id, closing the IDOR window
    token, label = await trash.soft_delete_set(conn, uid, set_id)
    await conn.commit()
    return _undo_response(token, label)
