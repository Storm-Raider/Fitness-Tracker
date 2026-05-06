import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.db import open_db, set_db, clear_db
from app.routes import dashboard, exercises, export, import_, metrics, webhooks, workouts
from app.routes.auth import router as auth_router, verify_session
from app.routes.workouts import set_http_client

logging.basicConfig(level=logging.INFO)

DATABASE_PATH = os.environ.get("DATABASE_PATH", "/data/fitness.db")

_EXEMPT_PATHS = {"/health", "/login", "/logout"}


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        cookie = request.cookies.get("fittrack_session", "")
        if cookie and verify_session(cookie):
            return await call_next(request)
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_password = os.environ.get("APP_PASSWORD", "")
    app_secret = os.environ.get("APP_SECRET", "")
    session_days_str = os.environ.get("SESSION_DAYS", "30")

    if not app_password:
        raise RuntimeError("APP_PASSWORD environment variable is required")
    if not app_secret:
        raise RuntimeError("APP_SECRET environment variable is required")
    if len(app_secret) < 32:
        raise RuntimeError("APP_SECRET must be at least 32 characters (generate with: python -c \"import secrets; print(secrets.token_hex(32))\")")
    try:
        session_days = int(session_days_str)
    except ValueError:
        raise RuntimeError(f"SESSION_DAYS must be an integer, got: {session_days_str!r}")
    if not (1 <= session_days <= 365):
        raise RuntimeError(f"SESSION_DAYS must be between 1 and 365, got: {session_days}")

    conn = await open_db(DATABASE_PATH)
    set_db(conn)
    client = httpx.AsyncClient()
    set_http_client(client)
    try:
        yield
    finally:
        await client.aclose()
        await conn.close()
        clear_db()


app = FastAPI(title="Fitness Tracker", lifespan=lifespan)
app.add_middleware(_AuthMiddleware)

_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=_static), name="static")

app.include_router(auth_router)
app.include_router(dashboard.router)
app.include_router(workouts.router)
app.include_router(exercises.router)
app.include_router(metrics.router)
app.include_router(export.router)
app.include_router(import_.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
