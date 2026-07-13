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

import app.db
from app.db import get_db
from app.utils.email import send_email
from app.utils.render import render, templates

router = APIRouter()

COOKIE_NAME = "zenkai_session"
_SALT = "zenkai-session"

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
                  COALESCE(us.pref_unit, 'kg') AS pref_unit,
                  COALESCE(us.pref_distance, 'km') AS pref_distance,
                  COALESCE(us.pref_body_measurement, 'cm') AS pref_body_measurement
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


class DistancePref(BaseModel):
    distance: str

@router.patch("/api/settings/distance")
async def patch_distance_pref(
    body: DistancePref,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from fastapi.responses import JSONResponse
    if body.distance not in ("km", "mi"):
        raise HTTPException(status_code=422, detail="distance must be 'km' or 'mi'")
    uid = current_user["id"]
    await conn.execute(
        """INSERT INTO user_settings(user_id, pref_distance)
           VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET pref_distance = excluded.pref_distance""",
        (uid, body.distance),
    )
    await conn.commit()
    return JSONResponse({"distance": body.distance})


class BodyMeasurementPref(BaseModel):
    unit: str

@router.patch("/api/settings/body-measurement")
async def patch_body_measurement_pref(
    body: BodyMeasurementPref,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if body.unit not in ("cm", "in"):
        raise HTTPException(status_code=422, detail="unit must be 'cm' or 'in'")
    uid = current_user["id"]
    await conn.execute(
        """INSERT INTO user_settings(user_id, pref_body_measurement)
           VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET pref_body_measurement = excluded.pref_body_measurement""",
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
        "SELECT id, password_hash FROM users WHERE LOWER(username) = LOWER(?)", (username,)
    ) as cur:
        row = await cur.fetchone()

    if not row or not _verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid username or password", "next": next, "prefill_username": username},
            status_code=401,
        )

    session_days = int(os.environ.get("SESSION_DAYS", "30"))

    # Create a server-side session record so logout can revoke it.
    sid = secrets.token_hex(32)
    await conn.execute(
        "INSERT INTO sessions(id, user_id, expires_at) "
        "VALUES (?, ?, datetime('now','localtime','+' || ? || ' days'))",
        (sid, row["id"], session_days),
    )
    # Lazily purge expired sessions for this user to keep the table lean.
    await conn.execute(
        "DELETE FROM sessions WHERE user_id=? AND expires_at <= datetime('now','localtime')",
        (row["id"],),
    )
    await conn.commit()

    dest = next if (next.startswith("/") and not next.startswith("//")) else "/"
    response = RedirectResponse(url=dest, status_code=303)
    token = _serializer().dumps({"user_id": row["id"], "sid": sid})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=session_days * 86400 if remember else None,
        httponly=True,
        samesite="strict",
        secure=True,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie:
        try:
            session_days = int(os.environ.get("SESSION_DAYS", "30"))
            payload = _serializer().loads(cookie, max_age=session_days * 86400)
            if isinstance(payload, dict) and "sid" in payload:
                await conn.execute("DELETE FROM sessions WHERE id=?", (payload["sid"],))
                await conn.commit()
        except Exception:
            pass  # malformed/expired cookie — nothing to revoke
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict", secure=True)
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
            f"Hi,\n\nClick the link below to reset your Zenkai password.\n"
            f"This link expires in 1 hour.\n\n{reset_url}\n\n"
            f"If you didn't request this, you can ignore this email."
        )
        body_html = (
            f"<p>Click the link below to reset your Zenkai password.<br>"
            f"This link expires in 1 hour.</p>"
            f'<p><a href="{reset_url}">{reset_url}</a></p>'
            f"<p>If you didn't request this, you can ignore this email.</p>"
        )
        await send_email(email, "Reset your Zenkai password", body_text, body_html)

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
    await conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
    await conn.commit()
    return RedirectResponse(url="/login?reset=1", status_code=303)


# ── Invite ────────────────────────────────────────────────────────────────────

async def _fetch_valid_invite(conn: aiosqlite.Connection, token: str) -> aiosqlite.Row:
    """Like _fetch_valid_token, but for the multi-use invite_tokens table:
    valid while uses_count < max_uses (not used_at IS NULL) and not expired."""
    async with conn.execute(
        "SELECT * FROM invite_tokens WHERE token = ? AND uses_count < max_uses"
        " AND expires_at > datetime('now','localtime')",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired invite link")
    return row


@router.get("/invite", response_class=HTMLResponse)
async def invite_get(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
):
    async with conn.execute(
        "SELECT token, created_at, expires_at, max_uses, uses_count FROM invite_tokens "
        "WHERE uses_count < max_uses AND expires_at > datetime('now','localtime') "
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
    max_uses: int = Form(5, ge=1, le=50),
):
    token = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
        "VALUES (?, ?, datetime('now','localtime','+7 days'), ?)",
        (token, user["id"], max_uses),
    )
    await conn.commit()
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/invite/accept/{token}"
    async with conn.execute(
        "SELECT token, created_at, expires_at, max_uses, uses_count FROM invite_tokens "
        "WHERE uses_count < max_uses AND expires_at > datetime('now','localtime') "
        "ORDER BY created_at DESC"
    ) as cur:
        pending_invites = [dict(r) for r in await cur.fetchall()]
    return render(request, "invite", {
        "invite_url": invite_url,
        "max_uses": max_uses,
        "user": dict(user),
        "pending_invites": pending_invites,
    })


@router.get("/invite/accept/{token}", response_class=HTMLResponse)
async def invite_accept_get(
    token: str,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_invite(conn, token)
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
    await _fetch_valid_invite(conn, token)

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
    async with app.db.write_lock:
        async with conn.execute("BEGIN IMMEDIATE"):
            pass
        try:
            async with conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, email) VALUES (?, ?, 0, ?)",
                (username, hashed, email),
            ) as cur:
                new_user_id = cur.lastrowid
            update_cur = await conn.execute(
                "UPDATE invite_tokens "
                "SET uses_count = uses_count + 1, used_at = datetime('now','localtime') "
                "WHERE token = ? AND uses_count < max_uses AND expires_at > datetime('now','localtime')",
                (token,),
            )
            if update_cur.rowcount == 0:
                # Someone else claimed the last remaining slot between our
                # _fetch_valid_invite check and this update — don't leave a user
                # row behind with no valid invite backing it. The rollback for
                # this and any other unexpected error is handled uniformly by
                # the catch-all `except Exception` below.
                raise HTTPException(status_code=400, detail="Invalid or expired invite link")
            await conn.execute("COMMIT")
        except aiosqlite.IntegrityError as exc:
            await conn.execute("ROLLBACK")
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
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    return RedirectResponse(url=f"/login?username={username}", status_code=302)
