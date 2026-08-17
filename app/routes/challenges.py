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


MAX_CUSTOM_DAYS = 365
MAX_TITLE_LEN = 80
_ALLOWED_FREEFORM_KINDS = {"manual", "photo"}  # "workout" is meaningful only on
# built-in presets that ship it as a default rule — a freeform (custom)
# challenge has no workout-data relationship to auto-tick from.


class StartIn(BaseModel):
    template_key: str
    start_date: str | None = None    # ISO date; defaults to today when omitted
    rules: list[dict] | None = None  # custom rules for editable challenges
    title: str | None = None         # required only for is_freeform templates
    total_days: int | None = None    # required only for is_freeform templates


class CheckinIn(BaseModel):
    day_date: str
    rule_key: str
    done: bool


class UpdateRulesIn(BaseModel):
    rules: list[dict]


class SubmitPartialIn(BaseModel):
    day_date: str


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
        "today_iso": today.isoformat(),
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
    is_freeform = bool(template.get("is_freeform"))

    # Validate optional back-dated start.
    if body.start_date is not None:
        try:
            sd = date.fromisoformat(body.start_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid start_date; expected YYYY-MM-DD")
        if sd > date.today():
            raise HTTPException(status_code=422, detail="start_date cannot be in the future")
    started_on = body.start_date or date.today().isoformat()

    # Freeform (custom) templates carry their own title/total_days per attempt —
    # every other template's identity is fixed, so these fields stay unused there.
    title = template["name"]
    total_days = template["total_days"]
    if is_freeform:
        title = (body.title or "").strip()
        if not title or len(title) > MAX_TITLE_LEN:
            raise HTTPException(
                status_code=422,
                detail=f"Title is required and must be 1-{MAX_TITLE_LEN} characters",
            )
        if body.total_days is None or not (1 <= body.total_days <= MAX_CUSTOM_DAYS):
            raise HTTPException(
                status_code=422,
                detail=f"total_days is required and must be 1-{MAX_CUSTOM_DAYS}",
            )
        total_days = body.total_days

    # Validate and store custom rules for editable challenges. Freeform templates
    # have no fallback rule list of their own, so rules are hard-required here —
    # without this, an omitted `rules` field would fall back to the template's
    # empty default list and every day would read as vacuously complete.
    rules_json = None
    if is_freeform or body.rules is not None:
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
            if is_freeform and r["kind"] not in _ALLOWED_FREEFORM_KINDS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Rule kind must be one of {sorted(_ALLOWED_FREEFORM_KINDS)}",
                )
        rules_json = json.dumps(body.rules)

    async with conn.execute(
        """INSERT INTO challenge_attempts(user_id, template_key, title, total_days, started_on, rules_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, template["key"], title, total_days, started_on, rules_json),
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
        and not ch.day_full_complete(view["_template"], yesterday.isoformat(), view["_checks"], view["_train_dates"])
    )

    # Back-dated catch-up: days from started_on up to two days ago that predate
    # creation_date. These couldn't have been logged before the challenge existed.
    creation_date = date.fromisoformat(row["created_at"][:10])
    backfill_days = []
    if start < creation_date and view["status"] == "active":
        d = start
        cutoff = today - timedelta(days=ch.GRACE_DAYS + 1)  # yesterday handled separately
        while d <= min(cutoff, last_day):
            backfill_days.append({
                "date": d.isoformat(),
                "day_n": (d - start).days + 1,
                "rules": ch.today_rules(view, d),
                "complete": ch.day_complete(view["_template"], d.isoformat(), view["_checks"], view["_train_dates"]),
            })
            d += timedelta(days=1)

    template = view["_template"]
    allow_partial = bool(template and template.get("allow_partial"))
    checks = view["_checks"]
    today_partial_submitted = checks.get(today.isoformat(), {}).get("_submitted", False)
    yesterday_partial_submitted = checks.get(yesterday.isoformat(), {}).get("_submitted", False)
    return templates.TemplateResponse(request, "challenge_detail.html", {
        "user": dict(current_user),
        "c": view,
        "tagline": template.get("tagline", "") if template else "",
        "today_iso": today.isoformat(),
        "today_in_range": start <= today <= last_day,
        "today_rules": ch.today_rules(view, today),
        "today_done": today_done,
        "today_total": today_total,
        "show_yesterday": show_yesterday,
        "yesterday_iso": yesterday.isoformat(),
        "yesterday_rules": ch.today_rules(view, yesterday) if show_yesterday else [],
        "backfill_days": backfill_days,
        "cells": ch.day_cells(view, today),
        "is_editable": bool(template and template.get("editable")),
        "is_freeform": bool(template and template.get("is_freeform")),
        "current_rules": template["rules"] if template else [],
        "is_partial_allowed": allow_partial,
        "today_partial_submitted": today_partial_submitted,
        "yesterday_partial_submitted": yesterday_partial_submitted,
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
    started_on = date.fromisoformat(row["started_on"])
    creation_date = date.fromisoformat(row["created_at"][:10])

    if started_on < creation_date:
        # Back-dated challenge: every day from started_on through today is editable
        # because the user couldn't have logged check-ins before registering.
        last_day = started_on + timedelta(days=row["total_days"] - 1)
        try:
            requested = date.fromisoformat(body.day_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid day_date format")
        if not (started_on <= requested <= min(today, last_day)):
            raise HTTPException(status_code=422, detail="That day is outside the challenge range")
    else:
        # Normal challenge: only today and yesterday (grace) are editable.
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


@router.post("/challenges/{attempt_id}/submit-partial")
async def submit_partial(
    attempt_id: int,
    body: SubmitPartialIn,
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
    if not template or not template.get("allow_partial"):
        raise HTTPException(status_code=422, detail="This challenge does not support partial submission")

    today = ch.today_local()
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
    rules["_submitted"] = True

    await conn.execute(
        """INSERT INTO challenge_checkins(attempt_id, user_id, day_date, rules_json, updated_at)
           VALUES (?, ?, ?, ?, datetime('now','localtime'))
           ON CONFLICT(attempt_id, day_date)
           DO UPDATE SET rules_json=excluded.rules_json, updated_at=excluded.updated_at""",
        (attempt_id, uid, body.day_date, json.dumps(rules)),
    )
    await conn.commit()

    row = await _attempt_row(conn, attempt_id, uid)
    view = await ch.evaluate_attempt(conn, row, today)
    day = date.fromisoformat(body.day_date)
    done, total = ch.rules_done_count(view, day)
    return JSONResponse({
        "day_date": body.day_date,
        "submitted": True,
        "day_done": done,
        "day_total": total,
        "status": view["status"],
    })


@router.post("/challenges/{attempt_id}/rules")
async def update_rules(
    attempt_id: int,
    body: UpdateRulesIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    row = await _attempt_row(conn, attempt_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if row["status"] != "active":
        raise HTTPException(status_code=409, detail="Challenge is no longer active")
    template = CHALLENGE_INDEX.get(row["template_key"])
    if not template or not template.get("editable"):
        raise HTTPException(status_code=422, detail="This challenge does not support custom rules")
    if not body.rules:
        raise HTTPException(status_code=422, detail="At least one rule is required")
    is_freeform = bool(template.get("is_freeform"))
    seen_keys: set[str] = set()
    for r in body.rules:
        if not isinstance(r.get("key"), str) or not isinstance(r.get("label"), str) or not r["label"].strip():
            raise HTTPException(status_code=422, detail="Each rule must have a non-empty key and label")
        if r["key"] in seen_keys:
            raise HTTPException(status_code=422, detail=f"Duplicate rule key: {r['key']}")
        seen_keys.add(r["key"])
        r.setdefault("kind", "manual")
        if is_freeform and r["kind"] not in _ALLOWED_FREEFORM_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"Rule kind must be one of {sorted(_ALLOWED_FREEFORM_KINDS)}",
            )
    await conn.execute(
        "UPDATE challenge_attempts SET rules_json=? WHERE id=? AND user_id=?",
        (json.dumps(body.rules), attempt_id, uid),
    )
    await conn.commit()
    return JSONResponse({"ok": True, "rule_count": len(body.rules)})


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
    # Freeform (custom) templates have no fixed title/total_days of their own —
    # every attempt shares one generic template entry, so restarting must carry
    # over the ORIGINAL attempt's title/total_days, not the template's unused
    # placeholder values. Fixed here because this was previously sourced
    # unconditionally from the template, which is only correct for 75 Hard/75
    # Medium where every attempt of a template really does share one identity.
    if template.get("is_freeform"):
        title, total_days = row["title"], row["total_days"]
    else:
        title, total_days = template["name"], template["total_days"]
    async with conn.execute(
        """INSERT INTO challenge_attempts(user_id, template_key, title, total_days, started_on, rules_json)
           VALUES (?, ?, ?, ?, date('now','localtime'), ?)""",
        (uid, template["key"], title, total_days, row.get("rules_json")),
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
