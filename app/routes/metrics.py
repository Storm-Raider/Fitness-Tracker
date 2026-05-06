import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import get_db
from app.utils.render import render

router = APIRouter()


class MetricIn(BaseModel):
    weight_kg: float
    calories: int | None = None


@router.get("/metrics")
async def list_metrics(
    request: Request, conn: aiosqlite.Connection = Depends(get_db)
):
    async with conn.execute(
        "SELECT id, recorded_at, weight_kg, calories FROM body_metrics "
        "WHERE user_id = 1 ORDER BY recorded_at DESC"
    ) as cur:
        metrics = [dict(r) for r in await cur.fetchall()]
    return render(request, "metrics", {"metrics": metrics})


@router.post("/metrics", status_code=201)
async def create_metric(
    body: MetricIn, conn: aiosqlite.Connection = Depends(get_db)
):
    async with conn.execute(
        "INSERT INTO body_metrics(weight_kg, calories, user_id) VALUES (?, ?, 1)",
        (body.weight_kg, body.calories),
    ) as cur:
        metric_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": metric_id}, status_code=201)
