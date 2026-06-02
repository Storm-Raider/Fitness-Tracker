from datetime import date, timedelta

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.render import templates

router = APIRouter()


class LogIn(BaseModel):
    log_date: str
    day_number: int | None = None
    weight_kg: float | None = Field(default=None, ge=0, le=500)
    workout: str | None = Field(default=None, max_length=200)
    meal_1: str | None = Field(default=None, max_length=300)
    meal_2: str | None = Field(default=None, max_length=300)
    meal_3: str | None = Field(default=None, max_length=300)
    water_l: float | None = Field(default=None, ge=0, le=20)
    energy: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    motivation: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    sleep_hrs: float | None = Field(default=None, ge=0, le=24)
    steps: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


@router.get("/journal", response_class=HTMLResponse)
async def journal_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    today = date.today().isoformat()

    # Today's entry (if any)
    async with conn.execute(
        "SELECT * FROM daily_logs WHERE user_id=? AND log_date=?", (uid, today)
    ) as c:
        row = await c.fetchone()
    today_log = dict(row) if row else None

    # Active challenge day number for auto-population
    active_day = None
    async with conn.execute(
        "SELECT started_on, total_days FROM challenge_attempts "
        "WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
        (uid,),
    ) as c:
        attempt = await c.fetchone()
    if attempt:
        start = date.fromisoformat(attempt["started_on"])
        day_n = (date.today() - start).days + 1
        if 1 <= day_n <= attempt["total_days"]:
            active_day = day_n

    # Last 60 days of history
    async with conn.execute(
        "SELECT * FROM daily_logs WHERE user_id=? ORDER BY log_date DESC LIMIT 60",
        (uid,),
    ) as c:
        history = [dict(r) for r in await c.fetchall()]

    return templates.TemplateResponse(request, "journal.html", {
        "user": dict(current_user),
        "today": today,
        "today_log": today_log,
        "active_day": active_day,
        "history": history,
    })


@router.post("/journal")
async def save_log(
    body: LogIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await conn.execute(
        """INSERT INTO daily_logs
               (user_id, log_date, day_number, weight_kg, workout,
                meal_1, meal_2, meal_3, water_l,
                energy, motivation, sleep_hrs, steps, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id, log_date) DO UPDATE SET
               day_number=excluded.day_number,
               weight_kg=excluded.weight_kg,
               workout=excluded.workout,
               meal_1=excluded.meal_1,
               meal_2=excluded.meal_2,
               meal_3=excluded.meal_3,
               water_l=excluded.water_l,
               energy=excluded.energy,
               motivation=excluded.motivation,
               sleep_hrs=excluded.sleep_hrs,
               steps=excluded.steps,
               notes=excluded.notes""",
        (uid, body.log_date, body.day_number, body.weight_kg, body.workout,
         body.meal_1, body.meal_2, body.meal_3, body.water_l,
         body.energy, body.motivation, body.sleep_hrs, body.steps, body.notes),
    )
    await conn.commit()
    return JSONResponse({"ok": True})
