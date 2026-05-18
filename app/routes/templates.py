import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.db_utils import require_owns
from app.utils.render import templates

router = APIRouter()


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    workout_id: int


@router.get("/templates", response_class=HTMLResponse)
async def list_templates(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id, name, created_at FROM workout_templates WHERE user_id = ? ORDER BY created_at DESC",
        (uid,),
    ) as cur:
        tmpl_rows = [dict(r) for r in await cur.fetchall()]

    result = []
    for t in tmpl_rows:
        async with conn.execute(
            """SELECT e.name FROM workout_template_exercises wte
               JOIN exercises e ON e.id = wte.exercise_id
               WHERE wte.template_id = ? ORDER BY wte.order_idx""",
            (t["id"],),
        ) as cur:
            t["exercises"] = [r["name"] for r in await cur.fetchall()]
        result.append(t)

    return templates.TemplateResponse(request, "templates.html", {
        "workout_templates": result,
        "user": dict(current_user),
    })


@router.post("/templates", status_code=201)
async def create_template(
    body: TemplateIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await require_owns(conn, "workouts", body.workout_id, uid)

    async with conn.execute(
        "INSERT INTO workout_templates(user_id, name) VALUES(?,?)",
        (uid, body.name.strip()),
    ) as cur:
        tmpl_id = cur.lastrowid

    async with conn.execute(
        """SELECT DISTINCT s.exercise_id, MIN(s.id) AS first_set
           FROM sets s WHERE s.workout_id = ? AND s.user_id = ?
           GROUP BY s.exercise_id ORDER BY first_set""",
        (body.workout_id, uid),
    ) as cur:
        exercises = await cur.fetchall()

    for idx, row in enumerate(exercises):
        await conn.execute(
            "INSERT INTO workout_template_exercises(template_id, exercise_id, order_idx) VALUES(?,?,?)",
            (tmpl_id, row["exercise_id"], idx),
        )

    await conn.commit()
    return JSONResponse({"id": tmpl_id}, status_code=201)


@router.delete("/templates/{template_id}", status_code=200)
async def delete_template(
    template_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await require_owns(conn, "workout_templates", template_id, current_user["id"])
    await conn.execute("DELETE FROM workout_templates WHERE id = ?", (template_id,))
    await conn.commit()
    return ""


@router.post("/workouts/from-template/{template_id}")
async def start_from_template(
    template_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    await require_owns(conn, "workout_templates", template_id, uid)

    async with conn.execute(
        "INSERT INTO workouts(user_id) VALUES(?)", (uid,)
    ) as cur:
        workout_id = cur.lastrowid
    await conn.commit()
    return RedirectResponse(f"/workouts/{workout_id}?tpl={template_id}", status_code=303)
