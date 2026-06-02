"""
Feedback — file a bug report or feature request straight to GitHub Issues.

The form lets the user pick a type (bug / feature), give a title and a
description, and submits it as a GitHub issue via the repo's API. The label is
chosen from the type: bug -> "bug", feature -> "enhancement".

Configuration (env vars, both required to enable submission):
  GITHUB_TOKEN  a personal access token with `repo` (or `public_repo`) scope
  GITHUB_REPO   "owner/name" (defaults to this project's repo)

If the token is missing the page renders in a read-only "not configured" state
and POSTs are rejected — nothing about the user's GitHub setup is assumed.
"""

import os
import time
from datetime import datetime
from threading import Lock

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.routes.workouts import get_http_client
from app.utils.render import templates

router = APIRouter()

import logging
logger = logging.getLogger(__name__)

_DEFAULT_REPO = "Storm-Raider/Fitness-Tracker"

# bug/feature -> (GitHub label, title prefix)
_TYPE_LABEL = {"bug": "bug", "feature": "enhancement"}
_TYPE_PREFIX = {"bug": "[Bug]", "feature": "[Feature]"}

# Light per-user cooldown so a stuck tab or double-tap can't spam the repo.
_last_submit: dict[int, float] = {}
_submit_lock = Lock()
_COOLDOWN_SECS = 30.0


def _github_config() -> tuple[str, str]:
    """(token, repo) — token is '' when reporting is not configured."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip() or _DEFAULT_REPO
    return token, repo


class FeedbackIn(BaseModel):
    type: str = Field(pattern=r"^(bug|feature)$")
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=4000)
    page: str = Field(default="", max_length=200)


@router.get("/feedback")
async def feedback_page(
    request: Request,
    current_user=Depends(get_current_user),
):
    token, repo = _github_config()
    return templates.TemplateResponse(request, "feedback.html", {
        "user": dict(current_user),
        "configured": bool(token),
        "repo": repo,
    })


@router.post("/feedback", status_code=201)
async def submit_feedback(
    body: FeedbackIn,
    current_user=Depends(get_current_user),
    conn=Depends(get_db),  # not used directly, keeps the auth/DB dependency chain uniform
):
    token, repo = _github_config()
    if not token:
        raise HTTPException(status_code=503, detail="Bug reporting isn't configured on this server.")

    client = get_http_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Reporting is temporarily unavailable.")

    uid = current_user["id"]
    now = time.monotonic()
    with _submit_lock:
        last = _last_submit.get(uid, 0.0)
        if now - last < _COOLDOWN_SECS:
            raise HTTPException(status_code=429, detail="Please wait a moment before submitting again.")
        _last_submit[uid] = now

    label = _TYPE_LABEL[body.type]
    prefix = _TYPE_PREFIX[body.type]
    title = f"{prefix} {body.title.strip()}"

    # Footer carries context so issues are actionable without leaking secrets.
    footer = (
        "\n\n---\n"
        f"Reported by **{current_user['username']}** via in-app feedback\n"
        f"Page: `{body.page or 'n/a'}`\n"
        f"Submitted: {datetime.now().isoformat(timespec='seconds')}"
    )
    issue_body = body.description.strip() + footer

    payload = {"title": title[:200], "body": issue_body, "labels": [label]}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        resp = await client.post(
            f"https://api.github.com/repos/{repo}/issues",
            json=payload, headers=headers, timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("GitHub issue create failed (network): %s", exc)
        raise HTTPException(status_code=502, detail="Couldn't reach GitHub. Try again later.")

    if resp.status_code == 201:
        data = resp.json()
        return JSONResponse(
            {"ok": True, "url": data.get("html_url"), "number": data.get("number")},
            status_code=201,
        )

    # Don't surface the raw GitHub error (may include rate-limit/token detail).
    logger.warning("GitHub issue create failed: %d %.200s", resp.status_code, resp.text)
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=502, detail="GitHub rejected the request — check the server token.")
    raise HTTPException(status_code=502, detail="GitHub couldn't create the issue. Try again later.")
