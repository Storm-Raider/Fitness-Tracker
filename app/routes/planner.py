import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import render

router = APIRouter()


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    goal: str = Field(pattern=r"^(hypertrophy|strength|peaking)$")
    weeks: int = Field(ge=4, le=16)
    lifts: list[dict]  # [{name, e1rm_kg}]


@router.get("/planner")
async def planner_page(request: Request):
    return RedirectResponse("/plan", status_code=301)


@router.post("/planner/plans", status_code=201)
async def save_plan(
    body: PlanIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    plan_json = json.dumps({"lifts": body.lifts})
    async with conn.execute(
        "INSERT INTO mesocycle_plans(user_id,name,goal,weeks,plan_json) VALUES (?,?,?,?,?)",
        (uid, body.name, body.goal, body.weeks, plan_json),
    ) as c:
        plan_id = c.lastrowid
    await conn.commit()
    return JSONResponse({"id": plan_id}, status_code=201)


@router.delete("/planner/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM mesocycle_plans WHERE id=? AND user_id=?",
        (plan_id, current_user["id"]),
    ) as c:
        if not await c.fetchone():
            raise HTTPException(status_code=404, detail="Plan not found")
    await conn.execute("DELETE FROM mesocycle_plans WHERE id=?", (plan_id,))
    await conn.commit()
