import os
import re
import secrets
import time
from collections import defaultdict
from threading import Lock

import aiosqlite
import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer

from app.db import get_db
from app.utils.render import render, templates

router = APIRouter()

COOKIE_NAME = "fittrack_session"
_SALT = "fittrack-session"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")

# ── Login rate limiter ────────────────────────────────────────────────────────
_attempts: dict[str, list[float]] = defaultdict(list)
_attempts_lock = Lock()
_MAX_ATTEMPTS = 10
_WINDOW_SECS = 900.0  # 15 minutes


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        bucket = [t for t in _attempts[ip] if now - t < _WINDOW_SECS]
        _attempts[ip] = bucket
        if len(bucket) >= _MAX_ATTEMPTS:
            return True
        bucket.append(now)
        return False


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(os.environ["APP_SECRET"], salt=_SALT)


async def get_current_user(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(status_code=401)
    async with conn.execute(
        "SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401)
    return row


async def require_admin(user=Depends(get_current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403)
    return user


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str = "/", username: str = ""):
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "next": next, "prefill_username": username},
    )


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    conn: aiosqlite.Connection = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Too many login attempts. Please wait 15 minutes.", "next": next, "prefill_username": username},
            status_code=429,
        )

    async with conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?", (username,)
    ) as cur:
        row = await cur.fetchone()

    if not row or not _verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid username or password", "next": next, "prefill_username": username},
            status_code=401,
        )

    session_days = int(os.environ.get("SESSION_DAYS", "30"))
    dest = next if next.startswith("/") else "/"
    response = RedirectResponse(url=dest, status_code=303)
    token = _serializer().dumps({"user_id": row["id"]})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=session_days * 86400,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict")
    return response


@router.get("/invite", response_class=HTMLResponse)
async def invite_get(
    request: Request,
    user=Depends(require_admin),
):
    return templates.TemplateResponse(
        request, "invite.html", {"user": dict(user)}
    )


@router.post("/invite")
async def invite_post(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
):
    token = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES (?, ?, datetime('now','localtime','+48 hours'))",
        (token, user["id"]),
    )
    await conn.commit()
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/invite/accept/{token}"
    return render(request, "invite", {"invite_url": invite_url, "user": dict(user)})


@router.get("/invite/accept/{token}", response_class=HTMLResponse)
async def invite_accept_get(
    token: str,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    async with conn.execute(
        "SELECT token FROM invite_tokens "
        "WHERE token = ? AND used_at IS NULL AND expires_at > datetime('now','localtime')",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired invite link")
    return templates.TemplateResponse(
        request, "invite_accept.html",
        {"token": token, "errors": {}, "form": {}},
    )


@router.post("/invite/accept/{token}")
async def invite_accept_post(
    token: str,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
):
    async with conn.execute(
        "SELECT token FROM invite_tokens "
        "WHERE token = ? AND used_at IS NULL AND expires_at > datetime('now','localtime')",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired invite link")

    errors = {}
    if not _USERNAME_RE.match(username):
        errors["username"] = "Username must be 3–30 characters: letters, numbers, underscores only"
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters"
    elif password != password_confirm:
        errors["password_confirm"] = "Passwords do not match"

    if errors:
        return templates.TemplateResponse(
            request, "invite_accept.html",
            {"token": token, "errors": errors, "form": {"username": username}},
            status_code=200,
        )

    hashed = _hash_password(password)
    try:
        async with conn.execute(
            "INSERT INTO users(username, password_hash, is_admin) VALUES (?, ?, 0)",
            (username, hashed),
        ) as cur:
            new_user_id = cur.lastrowid
        await conn.execute(
            "UPDATE invite_tokens "
            "SET used_at = datetime('now','localtime'), used_by = ? "
            "WHERE token = ?",
            (new_user_id, token),
        )
        await conn.commit()
    except aiosqlite.IntegrityError:
        errors["username"] = "Username is already taken"
        return templates.TemplateResponse(
            request, "invite_accept.html",
            {"token": token, "errors": errors, "form": {"username": username}},
            status_code=200,
        )

    return RedirectResponse(url=f"/login?username={username}", status_code=302)
