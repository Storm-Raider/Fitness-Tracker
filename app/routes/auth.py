import os
import re
import secrets
import time
from collections import defaultdict
from threading import Lock

import aiosqlite
import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer

from app.db import get_db
from app.utils.email import send_email
from app.utils.render import render, templates

router = APIRouter()

COOKIE_NAME = "fitstorm_session"
_SALT = "fitstorm-session"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

# ── Rate limiter (login + forgot-password) ────────────────────────────────────
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
        """SELECT u.id, u.username, u.is_admin,
                  COALESCE(us.pref_unit, 'kg') AS pref_unit
           FROM users u
           LEFT JOIN user_settings us ON us.user_id = u.id
           WHERE u.id = ?""",
        (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401)
    return row


async def require_admin(user=Depends(get_current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403)
    return user


from pydantic import BaseModel

class UnitPref(BaseModel):
    unit: str

@router.patch("/api/settings/unit")
async def patch_unit_pref(
    body: UnitPref,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if body.unit not in ("kg", "lbs"):
        raise HTTPException(status_code=422, detail="unit must be 'kg' or 'lbs'")
    uid = current_user["id"]
    await conn.execute(
        """INSERT INTO user_settings(user_id, pref_unit)
           VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET pref_unit = excluded.pref_unit""",
        (uid, body.unit),
    )
    await conn.commit()
    from fastapi.responses import JSONResponse
    return JSONResponse({"unit": body.unit})


# ── Login ─────────────────────────────────────────────────────────────────────

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
    remember: bool = Form(False),
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
    dest = next if (next.startswith("/") and not next.startswith("//")) else "/"
    response = RedirectResponse(url=dest, status_code=303)
    token = _serializer().dumps({"user_id": row["id"]})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=session_days * 86400 if remember else None,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict")
    return response


# ── Forgot / reset password ───────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_get(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": False, "error": None})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_post(
    request: Request,
    email: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        return templates.TemplateResponse(
            request, "forgot_password.html",
            {"sent": False, "error": "Too many requests. Please wait 15 minutes."},
            status_code=429,
        )

    email = email.strip().lower()

    # Always show the same success message to prevent email enumeration.
    async with conn.execute(
        "SELECT id FROM users WHERE lower(email) = ?", (email,)
    ) as cur:
        user_row = await cur.fetchone()

    if user_row:
        token = secrets.token_urlsafe(32)
        await conn.execute(
            "INSERT INTO password_reset_tokens(token, user_id, expires_at) "
            "VALUES (?, ?, datetime('now','localtime','+1 hour'))",
            (token, user_row["id"]),
        )
        await conn.commit()

        base = str(request.base_url).rstrip("/")
        reset_url = f"{base}/reset-password/{token}"
        body_text = (
            f"Hi,\n\nClick the link below to reset your FitStorm password.\n"
            f"This link expires in 1 hour.\n\n{reset_url}\n\n"
            f"If you didn't request this, you can ignore this email."
        )
        body_html = (
            f"<p>Click the link below to reset your FitStorm password.<br>"
            f"This link expires in 1 hour.</p>"
            f'<p><a href="{reset_url}">{reset_url}</a></p>'
            f"<p>If you didn't request this, you can ignore this email.</p>"
        )
        await send_email(email, "Reset your FitStorm password", body_text, body_html)

    return templates.TemplateResponse(request, "forgot_password.html", {"sent": True, "error": None})


async def _fetch_valid_token(
    conn: aiosqlite.Connection, table: str, token: str, detail: str
) -> aiosqlite.Row:
    async with conn.execute(
        f"SELECT * FROM {table} WHERE token = ? AND used_at IS NULL"
        f" AND expires_at > datetime('now','localtime')",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail=detail)
    return row


@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_get(
    token: str,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_token(conn, "password_reset_tokens", token, "Invalid or expired password reset link")
    return templates.TemplateResponse(request, "reset_password.html", {"token": token, "errors": {}})


@router.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_post(
    token: str,
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
):
    row = await _fetch_valid_token(conn, "password_reset_tokens", token, "Invalid or expired password reset link")

    errors = {}
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters"
    elif password != password_confirm:
        errors["password_confirm"] = "Passwords do not match"

    if errors:
        return templates.TemplateResponse(
            request, "reset_password.html", {"token": token, "errors": errors}
        )

    hashed = _hash_password(password)
    await conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (hashed, row["user_id"])
    )
    await conn.execute(
        "UPDATE password_reset_tokens SET used_at = datetime('now','localtime') WHERE token = ?",
        (token,),
    )
    await conn.commit()
    return RedirectResponse(url="/login?reset=1", status_code=303)


# ── Invite ────────────────────────────────────────────────────────────────────

@router.get("/invite", response_class=HTMLResponse)
async def invite_get(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
):
    async with conn.execute(
        "SELECT token, created_at, expires_at FROM invite_tokens "
        "WHERE used_at IS NULL AND expires_at > datetime('now','localtime') "
        "ORDER BY created_at DESC"
    ) as cur:
        pending_invites = [dict(r) for r in await cur.fetchall()]
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        request, "invite.html", {"user": dict(user), "pending_invites": pending_invites, "base_url": base}
    )


@router.delete("/invite/{token}")
async def invite_delete(
    token: str,
    conn: aiosqlite.Connection = Depends(get_db),
    _user=Depends(require_admin),
):
    await conn.execute("DELETE FROM invite_tokens WHERE token = ?", (token,))
    await conn.commit()
    return Response(status_code=200)


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
    await _fetch_valid_token(conn, "invite_tokens", token, "Invalid or expired invite link")
    return templates.TemplateResponse(
        request, "invite_accept.html",
        {"token": token, "errors": {}, "form": {}},
    )


@router.post("/invite/accept/{token}")
async def invite_accept_post(
    token: str,
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_token(conn, "invite_tokens", token, "Invalid or expired invite link")

    email = email.strip().lower()
    errors = {}
    if not _USERNAME_RE.match(username):
        errors["username"] = "Username must be 3–30 characters: letters, numbers, underscores only"
    if not _EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address"
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters"
    elif password != password_confirm:
        errors["password_confirm"] = "Passwords do not match"

    if errors:
        return templates.TemplateResponse(
            request, "invite_accept.html",
            {"token": token, "errors": errors, "form": {"username": username, "email": email}},
            status_code=200,
        )

    hashed = _hash_password(password)
    try:
        async with conn.execute(
            "INSERT INTO users(username, password_hash, is_admin, email) VALUES (?, ?, 0, ?)",
            (username, hashed, email),
        ) as cur:
            new_user_id = cur.lastrowid
        await conn.execute(
            "UPDATE invite_tokens "
            "SET used_at = datetime('now','localtime'), used_by = ? "
            "WHERE token = ?",
            (new_user_id, token),
        )
        await conn.commit()
    except aiosqlite.IntegrityError as exc:
        msg = str(exc)
        if "email" in msg:
            errors["email"] = "An account with that email already exists"
        else:
            errors["username"] = "Username is already taken"
        return templates.TemplateResponse(
            request, "invite_accept.html",
            {"token": token, "errors": errors, "form": {"username": username, "email": email}},
            status_code=200,
        )

    return RedirectResponse(url=f"/login?username={username}", status_code=302)
