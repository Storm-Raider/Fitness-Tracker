import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.charts import generate_sparkline
from app.utils.render import render

router = APIRouter()


class MetricIn(BaseModel):
    weight_kg: float = Field(ge=1.0, le=500.0)
    calories: int | None = Field(default=None, ge=0, le=50_000)


@router.get("/metrics")
async def list_metrics(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id, recorded_at, weight_kg, calories FROM body_metrics "
        "WHERE user_id = ? ORDER BY recorded_at DESC",
        (current_user["id"],),
    ) as cur:
        metrics = [dict(r) for r in await cur.fetchall()]

    # Build chart from chronological order (oldest first)
    chrono = list(reversed(metrics))
    chart_svg = generate_sparkline(
        values=[m["weight_kg"] for m in chrono],
        labels=[m["recorded_at"][:10] for m in chrono],
        color="#f59e0b",
        unit=" kg",
    )

    return render(
        request,
        "metrics",
        {"metrics": metrics, "chart_svg": chart_svg, "user": dict(current_user)},
    )


@router.post("/metrics", status_code=201)
async def create_metric(
    body: MetricIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "INSERT INTO body_metrics(weight_kg, calories, user_id) VALUES (?, ?, ?)",
        (body.weight_kg, body.calories, current_user["id"]),
    ) as cur:
        metric_id = cur.lastrowid
    await conn.commit()
    return JSONResponse({"id": metric_id}, status_code=201)
