import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from app.db import open_db, set_db, clear_db
import app.db as _db
from app.routes import achievements, cardio, challenges, coach, dashboard, exercises, export, feedback, import_, journal, metrics, planner, prs, routines, settings, stats, templates, trash, webhooks, workouts
from app.routes.auth import router as auth_router, COOKIE_NAME, _serializer, _hash_password, _verify_password
from app.routes.workouts import set_http_client

from itsdangerous import BadSignature, SignatureExpired

logging.basicConfig(level=logging.INFO)

_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

DATABASE_PATH = os.environ.get("DATABASE_PATH", "/data/fitness.db")

_EXEMPT_PATHS = {"/health", "/login", "/logout", "/sw.js", "/forgot-password"}


def _is_exempt(path: str) -> bool:
    return (
        path in _EXEMPT_PATHS
        or path.startswith("/static/")
        or path.startswith("/invite/accept/")
        or path.startswith("/reset-password/")
    )


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        return response


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)
        cookie = request.cookies.get(COOKIE_NAME, "")
        if cookie:
            try:
                session_days = int(os.environ.get("SESSION_DAYS", "30"))
                payload = _serializer().loads(cookie, max_age=session_days * 86400)
                if isinstance(payload, dict) and "user_id" in payload:
                    sid = payload.get("sid")
                    # Cookies without a sid are legacy (pre-revocation); reject them
                    # so existing sessions gracefully force a re-login once.
                    if sid and _db._conn is not None:
                        async with _db._conn.execute(
                            "SELECT 1 FROM sessions WHERE id=? AND user_id=? "
                            "AND expires_at > datetime('now','localtime')",
                            (sid, payload["user_id"]),
                        ) as cur:
                            if await cur.fetchone() is None:
                                sid = None  # revoked or expired
                    if sid:
                        request.state.user_id = payload["user_id"]
                        return await call_next(request)
            except (BadSignature, SignatureExpired):
                pass
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


@asynccontextmanager
async def lifespan(app: FastAPI):
    admin_username = os.environ.get("ADMIN_USERNAME", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    app_secret = os.environ.get("APP_SECRET", "")
    session_days_str = os.environ.get("SESSION_DAYS", "30")

    if not admin_username:
        raise RuntimeError("ADMIN_USERNAME environment variable is required")
    if not admin_password:
        raise RuntimeError("ADMIN_PASSWORD environment variable is required")
    if not app_secret:
        raise RuntimeError("APP_SECRET environment variable is required")
    if len(app_secret) < 32:
        raise RuntimeError(
            "APP_SECRET must be at least 32 characters "
            "(generate with: python -c \"import secrets; print(secrets.token_hex(32))\")"
        )
    try:
        session_days = int(session_days_str)
    except ValueError:
        raise RuntimeError(f"SESSION_DAYS must be an integer, got: {session_days_str!r}")
    if not (1 <= session_days <= 365):
        raise RuntimeError(f"SESSION_DAYS must be between 1 and 365, got: {session_days}")

    conn = await open_db(DATABASE_PATH)
    set_db(conn)

    # Seed admin user; always sync password_hash and is_admin from env vars
    # so the account is always correct after a restart regardless of what is
    # stored in the DB.  Checking is_admin separately from the password lets
    # us promote an existing non-admin account whose credentials now match the
    # env vars without requiring a password change first.
    async with conn.execute(
        "SELECT id, password_hash, is_admin FROM users WHERE LOWER(username) = LOWER(?)",
        (admin_username,),
    ) as cur:
        existing = await cur.fetchone()
    if not existing:
        hashed = _hash_password(admin_password)
        await conn.execute(
            "INSERT INTO users(username, password_hash, is_admin) VALUES (?, ?, 1)",
            (admin_username, hashed),
        )
        await conn.commit()
    else:
        password_ok = _verify_password(admin_password, existing["password_hash"])
        already_admin = bool(existing["is_admin"])
        if not password_ok or not already_admin:
            new_hash = (
                _hash_password(admin_password) if not password_ok
                else existing["password_hash"]
            )
            await conn.execute(
                "UPDATE users SET password_hash = ?, is_admin = 1 WHERE id = ?",
                (new_hash, existing["id"]),
            )
            await conn.commit()

    client = httpx.AsyncClient()
    set_http_client(client)
    try:
        yield
    finally:
        await client.aclose()
        await conn.close()
        clear_db()


app = FastAPI(title="Fitness Tracker", lifespan=lifespan)
app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_AuthMiddleware)


@app.exception_handler(404)
async def _not_found(_req: Request, _exc):
    return _templates.TemplateResponse("errors/404.html", {"request": _req}, status_code=404)


@app.exception_handler(500)
async def _server_error(_req: Request, _exc):
    return _templates.TemplateResponse("errors/500.html", {"request": _req}, status_code=500)

_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=_static), name="static")

app.include_router(auth_router)
app.include_router(cardio.router)
app.include_router(dashboard.router)
app.include_router(workouts.router)
app.include_router(exercises.router)
app.include_router(metrics.router)
app.include_router(journal.router)
app.include_router(feedback.router)
app.include_router(export.router)
app.include_router(import_.router)
app.include_router(webhooks.router)
app.include_router(routines.router)
app.include_router(settings.router)
app.include_router(stats.router)
app.include_router(prs.router)
app.include_router(templates.router)
app.include_router(achievements.router)
app.include_router(planner.router)
app.include_router(coach.router)
app.include_router(trash.router)
app.include_router(challenges.router)


@app.get("/health")
async def health():
    # Polled by scripts/auto-deploy-user.sh after each restart to confirm the
    # new code booted. Keep this cheap and dependency-free.
    return {"status": "ok"}


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    sw_path = Path(__file__).parent / "static" / "sw.js"
    return FileResponse(sw_path, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})
