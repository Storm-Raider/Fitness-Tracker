"""
Challenges — fixed-length daily-adherence programs (75 Hard / 75 Medium).

Reset/completion is computed lazily on read (see app/utils/challenges.py); no
background job. Progress photos are stored on-device (IndexedDB) by the client;
the server only records a done/not-done boolean per rule.
"""

import json
from datetime import date, timedelta

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.data.challenges import CHALLENGES, CHALLENGE_INDEX
from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import challenges as ch
from app.utils.render import templates

router = APIRouter()


class StartIn(BaseModel):
    template_key: str
    rules: list[dict] | None = None  # custom rules for editable challenges


class CheckinIn(BaseModel):
    day_date: str
    rule_key: str
    done: bool


async def _attempt_row(conn, attempt_id: int, uid: int) -> dict | None:
    async with conn.execute(
        "SELECT * FROM challenge_attempts WHERE id=? AND user_id=?", (attempt_id, uid)
    ) as c:
        r = await c.fetchone()
    return dict(r) if r else None


@router.get("/challenges", response_class=HTMLResponse)
async def challenges_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    today = ch.today_local()
    train = await ch.training_dates(conn, uid)

    async with conn.execute(
        "SELECT * FROM challenge_attempts WHERE user_id=? ORDER BY created_at DESC", (uid,)
    ) as c:
        rows = [dict(r) for r in await c.fetchall()]

    active, past = [], []
    for row in rows:
        view = await ch.evaluate_attempt(conn, row, today, train)
        if view["status"] == "active":
            done, total = ch.rules_done_count(view, today)
            active.append({**view, "today_done": done, "today_total": total})
        else:
            past.append(view)

    return templates.TemplateResponse(request, "challenges.html", {
        "user": dict(current_user),
        "presets": CHALLENGES,
        "active": active,
        "past": past,
    })


@router.post("/challenges", status_code=201)
async def start_challenge(
    body: StartIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    template = CHALLENGE_INDEX.get(body.template_key)
    if not template:
        raise HTTPException(status_code=404, detail="Unknown challenge")
    uid = current_user["id"]

    # Validate and store custom rules for editable challenges.
    rules_json = None
    if body.rules is not None:
        if not template.get("editable"):
            raise HTTPException(status_code=422, detail="This challenge does not support custom rules")
        if not body.rules:
            raise HTTPException(status_code=422, detail="At least one rule is required")
        # Ensure every rule has required fields and a unique key.
        seen_keys: set[str] = set()
        for r in body.rules:
            if not isinstance(r.get("key"), str) or not isinstance(r.get("label"), str) or not r["label"].strip():
                raise HTTPException(status_code=422, detail="Each rule must have a non-empty key and label")
            if r["key"] in seen_keys:
                raise HTTPException(status_code=422, detail=f"Duplicate rule key: {r['key']}")
            seen_keys.add(r["key"])
            r.setdefault("kind", "manual")
        rules_json = json.dumps(body.rules)

    async with conn.execute(
        """INSERT INTO challenge_attempts(user_id, template_key, title, total_days, started_on, rules_json)
           VALUES (?, ?, ?, ?, date('now','localtime'), ?)""",
        (uid, template["key"], template["name"], template["total_days"], rules_json),
    ) as c:
        attempt_id = c.lastrowid
    await conn.commit()
    return JSONResponse({"id": attempt_id}, status_code=201)


@router.get("/challenges/{attempt_id}", response_class=HTMLResponse)
async def challenge_detail(
    attempt_id: int,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    row = await _attempt_row(conn, attempt_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")

    today = ch.today_local()
    view = await ch.evaluate_attempt(conn, row, today)
    if not view["_template"]:
        raise HTTPException(status_code=404, detail="Unknown challenge template")

    start = date.fromisoformat(view["started_on"])
    last_day = start + timedelta(days=view["total_days"] - 1)
    today_done, today_total = ch.rules_done_count(view, today)

    # Yesterday is editable while in the grace window and still within the run.
    yesterday = today - timedelta(days=ch.GRACE_DAYS)
    show_yesterday = (
        view["status"] == "active"
        and start <= yesterday <= last_day
        and yesterday < today
        and not ch.day_complete(view["_template"], yesterday.isoformat(), view["_checks"], view["_train_dates"])
    )

    return templates.TemplateResponse(request, "challenge_detail.html", {
        "user": dict(current_user),
        "c": view,
        "tagline": view["_template"].get("tagline", ""),
        "today_iso": today.isoformat(),
        "today_in_range": start <= today <= last_day,
        "today_rules": ch.today_rules(view, today),
        "today_done": today_done,
        "today_total": today_total,
        "show_yesterday": show_yesterday,
        "yesterday_iso": yesterday.isoformat(),
        "yesterday_rules": ch.today_rules(view, yesterday) if show_yesterday else [],
        "cells": ch.day_cells(view, today),
    })


@router.post("/challenges/{attempt_id}/checkin")
async def checkin(
    attempt_id: int,
    body: CheckinIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    row = await _attempt_row(conn, attempt_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if row["status"] != "active":
        raise HTTPException(status_code=409, detail="This challenge is no longer active")

    template = CHALLENGE_INDEX.get(row["template_key"])
    effective_rules = ch.attempt_rules(row, template) if template else []
    if not template or body.rule_key not in {r["key"] for r in effective_rules}:
        raise HTTPException(status_code=422, detail="Unknown rule")

    today = ch.today_local()
    # Only today and yesterday (grace) are editable.
    editable = {today.isoformat(), (today - timedelta(days=ch.GRACE_DAYS)).isoformat()}
    if body.day_date not in editable:
        raise HTTPException(status_code=422, detail="That day can no longer be edited")

    async with conn.execute(
        "SELECT rules_json FROM challenge_checkins WHERE attempt_id=? AND day_date=?",
        (attempt_id, body.day_date),
    ) as c:
        existing = await c.fetchone()
    rules = {}
    if existing:
        try:
            rules = json.loads(existing["rules_json"])
        except (json.JSONDecodeError, TypeError):
            rules = {}
    rules[body.rule_key] = body.done

    await conn.execute(
        """INSERT INTO challenge_checkins(attempt_id, user_id, day_date, rules_json, updated_at)
           VALUES (?, ?, ?, ?, datetime('now','localtime'))
           ON CONFLICT(attempt_id, day_date)
           DO UPDATE SET rules_json=excluded.rules_json, updated_at=excluded.updated_at""",
        (attempt_id, uid, body.day_date, json.dumps(rules)),
    )
    await conn.commit()

    # Re-evaluate so the client can react to completion/reset immediately.
    row = await _attempt_row(conn, attempt_id, uid)
    view = await ch.evaluate_attempt(conn, row, today)
    day = date.fromisoformat(body.day_date)
    done, total = ch.rules_done_count(view, day)
    return JSONResponse({
        "day_date": body.day_date,
        "rule_key": body.rule_key,
        "done": body.done,
        "day_complete": done == total,
        "day_done": done,
        "day_total": total,
        "status": view["status"],
    })


@router.post("/challenges/{attempt_id}/restart", status_code=201)
async def restart_challenge(
    attempt_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    row = await _attempt_row(conn, attempt_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")
    template = CHALLENGE_INDEX.get(row["template_key"])
    if not template:
        raise HTTPException(status_code=404, detail="Unknown challenge template")
    async with conn.execute(
        """INSERT INTO challenge_attempts(user_id, template_key, title, total_days, started_on, rules_json)
           VALUES (?, ?, ?, ?, date('now','localtime'), ?)""",
        (uid, template["key"], template["name"], template["total_days"], row.get("rules_json")),
    ) as c:
        new_id = c.lastrowid
    await conn.commit()
    return JSONResponse({"id": new_id}, status_code=201)


@router.post("/challenges/{attempt_id}/abandon", status_code=200)
async def abandon_challenge(
    attempt_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    row = await _attempt_row(conn, attempt_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")
    await conn.execute(
        "UPDATE challenge_attempts SET status='abandoned', ended_on=date('now','localtime') "
        "WHERE id=? AND user_id=? AND status='active'",
        (attempt_id, uid),
    )
    await conn.commit()
    return JSONResponse({"status": "abandoned"})
