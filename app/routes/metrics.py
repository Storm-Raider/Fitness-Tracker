import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.db_utils import require_owns
from app.utils.render import render

router = APIRouter()

MEASUREMENT_SITES = ["Chest", "Waist", "Hips", "Left Arm", "Right Arm", "Thighs", "Calves", "Neck"]
_SITE_SET = {s.lower() for s in MEASUREMENT_SITES}


class MetricIn(BaseModel):
    weight_kg: float = Field(ge=1.0, le=500.0)
    calories: int | None = Field(default=None, ge=0, le=50_000)
    notes: str | None = Field(default=None, max_length=300)
    entry_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class MeasurementIn(BaseModel):
    site: str = Field(min_length=1, max_length=50)
    value_cm: float = Field(gt=0, le=500)
    logged_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str | None = Field(default=None, max_length=300)


@router.get("/metrics")
async def list_metrics(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id, recorded_at, COALESCE(entry_date, DATE(recorded_at)) AS entry_date, "
        "weight_kg, calories, notes FROM body_metrics "
        "WHERE user_id = ? ORDER BY COALESCE(entry_date, DATE(recorded_at)) DESC, recorded_at DESC",
        (uid,),
    ) as cur:
        metrics = [dict(r) for r in await cur.fetchall()]

    chrono = list(reversed(metrics))
    metrics_json = json.dumps([{"date": m["entry_date"], "weight_kg": m["weight_kg"]} for m in chrono])
    latest_weight = metrics[0]["weight_kg"] if metrics else None

    async with conn.execute(
        "SELECT id, logged_date, site, value_cm, notes FROM body_measurements "
        "WHERE user_id = ? ORDER BY logged_date DESC, id DESC",
        (uid,),
    ) as cur:
        measurements = [dict(r) for r in await cur.fetchall()]

    # Build per-site chronological series for the chart
    from collections import defaultdict
    site_series: dict[str, list] = defaultdict(list)
    for m in reversed(measurements):
        site_series[m["site"]].append({"date": m["logged_date"], "value": m["value_cm"]})
    measurements_json = json.dumps(dict(site_series))

    return render(
        request,
        "metrics",
        {
            "metrics": metrics,
            "metrics_json": metrics_json,
            "measurements": measurements,
            "measurements_json": measurements_json,
            "measurement_sites": MEASUREMENT_SITES,
            "user": dict(current_user),
            "latest_weight": latest_weight,
        },
    )


@router.delete("/metrics/{metric_id}", status_code=200)
async def delete_metric(
    metric_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await require_owns(conn, "body_metrics", metric_id, current_user["id"])
    await conn.execute(
        "DELETE FROM body_metrics WHERE id = ? AND user_id = ?",
        (metric_id, current_user["id"]),
    )
    await conn.commit()
    return ""


@router.post("/measurements", status_code=201)
async def create_measurement(
    body: MeasurementIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from datetime import date as _date
    if body.site.lower() not in _SITE_SET:
        raise HTTPException(status_code=400, detail="Unknown measurement site")
    logged_date = body.logged_date or _date.today().isoformat()
    async with conn.execute(
        "INSERT INTO body_measurements(user_id, site, value_cm, logged_date, notes) VALUES (?, ?, ?, ?, ?)",
        (current_user["id"], body.site, body.value_cm, logged_date, body.notes),
    ) as cur:
        mid = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": mid}, status_code=201)


@router.delete("/measurements/{measurement_id}", status_code=200)
async def delete_measurement(
    measurement_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM body_measurements WHERE id = ? AND user_id = ?",
        (measurement_id, current_user["id"]),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Measurement not found")
    await conn.execute(
        "DELETE FROM body_measurements WHERE id = ? AND user_id = ?",
        (measurement_id, current_user["id"]),
    )
    await conn.commit()
    return ""


@router.post("/metrics", status_code=201)
async def create_metric(
    body: MetricIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from datetime import date as _date
    entry_date = body.entry_date or _date.today().isoformat()
    async with conn.execute(
        "INSERT INTO body_metrics(weight_kg, calories, notes, entry_date, user_id) VALUES (?, ?, ?, ?, ?)",
        (body.weight_kg, body.calories, body.notes, entry_date, current_user["id"]),
    ) as cur:
        metric_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": metric_id}, status_code=201)
