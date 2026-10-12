"""Oura Ring: connect, callback, sync now, disconnect, today's numbers.

The callback is exempt from the login check (app/main.py): the session cookie is
SameSite=Strict, so the browser does not send it on Oura's cross-site redirect
back. The callback identifies the user from the signed, single-use `state`
instead, and hands over to Settings through a same-site page so the next
request carries the cookie again.
"""
import html
import os
import secrets
import time

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import oura, oura_sync

router = APIRouter()

STATE_MAX_AGE = 600               # seconds a Connect click stays valid
_pending: dict[str, tuple[int, float]] = {}    # nonce -> (uid, issued): single use
_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient) -> None:
    global _client
    _client = client


def http_client() -> httpx.AsyncClient | None:
    return _client


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(os.environ["APP_SECRET"], salt="oura-state")


def _require_configured() -> None:
    if not oura.is_configured():
        raise HTTPException(status_code=404, detail="Oura isn't set up on this server")


def _handoff(url: str) -> HTMLResponse:
    """Go to a Zenkai page from the callback with a same-site navigation (a 303
    would stay part of Oura's cross-site redirect, and the Strict cookie with it)."""
    u = html.escape(url, quote=True)
    return HTMLResponse(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={u}">'
        f'<title>Zenkai</title><p style="font-family:system-ui;padding:1rem">'
        f'<a href="{u}">Back to Zenkai</a></p>'
    )


@router.get("/oura/connect")
async def oura_connect(current_user=Depends(get_current_user)):
    _require_configured()
    now = time.time()
    for n, (_, issued) in list(_pending.items()):        # drop expired nonces
        if now - issued > STATE_MAX_AGE:
            _pending.pop(n, None)
    nonce = secrets.token_urlsafe(24)
    _pending[nonce] = (current_user["id"], now)
    state = _signer().dumps({"uid": current_user["id"], "n": nonce})
    return RedirectResponse(oura.authorize_url(state), status_code=302)


@router.get("/oura/callback")
async def oura_callback(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    _require_configured()
    state = request.query_params.get("state", "")
    try:
        data = _signer().loads(state, max_age=STATE_MAX_AGE)
        uid, nonce = int(data["uid"]), str(data["n"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return HTMLResponse("That Oura link expired or isn't valid. Go back to Settings and press Connect again.",
                            status_code=400)
    pending = _pending.pop(nonce, None)                     # single use
    if not pending or pending[0] != uid:
        return HTMLResponse("That Oura link was already used. Go back to Settings and press Connect again.",
                            status_code=400)
    if request.query_params.get("error") or not request.query_params.get("code"):
        return _handoff("/settings?oura=denied")
    client = http_client()
    if client is None:
        return _handoff("/settings?oura=failed")
    try:
        tokens = await oura.exchange_code(client, request.query_params["code"])
    except oura.OuraError:
        return _handoff("/settings?oura=failed")
    await oura_sync.save_tokens(conn, uid, tokens)
    await oura_sync.sync_user(conn, client, uid)            # backfill now: Settings shows the result
    return _handoff("/settings?oura=connected")


@router.post("/oura/sync")
async def oura_sync_now(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_configured()
    status = await oura_sync.sync_user(conn, http_client(), current_user["id"]) if http_client() else "unavailable"
    return JSONResponse({"status": status, "connection": await oura_sync.connection(conn, current_user["id"])})


class DisconnectIn(BaseModel):
    keep_data: bool = True


@router.post("/oura/disconnect")
async def oura_disconnect(
    body: DisconnectIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_configured()
    await oura_sync.disconnect(conn, http_client(), current_user["id"], body.keep_data)
    return JSONResponse({"ok": True})


@router.get("/api/oura/today")
async def oura_today(
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not oura.is_configured():
        return JSONResponse({"connected": False})
    uid = current_user["id"]
    status = await oura_sync.connection(conn, uid)
    if not status:
        return JSONResponse({"connected": False})
    day = await oura_sync.today_for(conn, uid)
    keys = ("day", "is_today", "readiness_score", "band", "sleep_score", "hrv_ms", "resting_hr", "sleep_hours", "steps")
    return JSONResponse({"connected": True, "needs_reconnect": bool(status["needs_reconnect"]),
                         **({k: day[k] for k in keys} if day else {"day": None})})
