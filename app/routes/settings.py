import re

import aiosqlite
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import (
    _EMAIL_RE,
    _hash_password,
    _verify_password,
    get_current_user,
)
from app.utils.render import templates

router = APIRouter()


class GoalIn(BaseModel):
    sessions: int = Field(ge=1, le=14)


@router.post("/settings/goal")
async def set_weekly_goal(
    body: GoalIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await conn.execute(
        "INSERT INTO user_settings(user_id, weekly_goal_sessions) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET weekly_goal_sessions=excluded.weekly_goal_sessions",
        (current_user["id"], body.sessions),
    )
    await conn.commit()
    return JSONResponse({"sessions": body.sessions})


@router.delete("/settings/goal", status_code=204)
async def delete_weekly_goal(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await conn.execute(
        "UPDATE user_settings SET weekly_goal_sessions = NULL WHERE user_id = ?",
        (current_user["id"],),
    )
    await conn.commit()


@router.get("/settings")
async def settings_get(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id, username, email FROM users WHERE id = ?", (current_user["id"],)
    ) as cur:
        user_row = dict(await cur.fetchone())

    success = request.query_params.get("success")
    return templates.TemplateResponse(request, "settings.html", {
        "user": dict(current_user),
        "profile": user_row,
        "success": success,
        "email_errors": {},
        "password_errors": {},
    })


@router.post("/settings/email")
async def settings_email_post(
    request: Request,
    email: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    email = email.strip().lower()
    errors = {}

    if not _EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address"

    if errors:
        async with conn.execute(
            "SELECT id, username, email FROM users WHERE id = ?", (current_user["id"],)
        ) as cur:
            user_row = dict(await cur.fetchone())
        return templates.TemplateResponse(request, "settings.html", {
            "user": dict(current_user),
            "profile": user_row,
            "success": None,
            "email_errors": errors,
            "password_errors": {},
        })

    try:
        await conn.execute(
            "UPDATE users SET email = ? WHERE id = ?", (email, current_user["id"])
        )
        await conn.commit()
    except aiosqlite.IntegrityError:
        async with conn.execute(
            "SELECT id, username, email FROM users WHERE id = ?", (current_user["id"],)
        ) as cur:
            user_row = dict(await cur.fetchone())
        return templates.TemplateResponse(request, "settings.html", {
            "user": dict(current_user),
            "profile": user_row,
            "success": None,
            "email_errors": {"email": "An account with that email already exists"},
            "password_errors": {},
        })

    return RedirectResponse(url="/settings?success=email", status_code=303)


@router.post("/settings/password")
async def settings_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id, username, email, password_hash FROM users WHERE id = ?",
        (current_user["id"],),
    ) as cur:
        user_row = dict(await cur.fetchone())

    errors = {}
    if not _verify_password(current_password, user_row["password_hash"]):
        errors["current_password"] = "Current password is incorrect"
    elif len(new_password) < 8:
        errors["new_password"] = "New password must be at least 8 characters"
    elif new_password != new_password_confirm:
        errors["new_password_confirm"] = "Passwords do not match"

    if errors:
        return templates.TemplateResponse(request, "settings.html", {
            "user": dict(current_user),
            "profile": user_row,
            "success": None,
            "email_errors": {},
            "password_errors": errors,
        })

    hashed = _hash_password(new_password)
    await conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (hashed, current_user["id"])
    )
    await conn.commit()
    return RedirectResponse(url="/settings?success=password", status_code=303)
