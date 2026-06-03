#!/usr/bin/env python3
"""
User-perspective smoke test — runs after the unit test suite in the nightly
triage pipeline, before any auto-fix is pushed to main.

Uses the same ASGI in-memory approach as tests/conftest.py so it exercises
the full Jinja render path without a live server. Checks that every core
user-facing page:
  - Returns HTTP 200
  - Contains expected landmark text (page title / key element)
  - Does NOT contain server-error indicators in the rendered HTML

Exit 0 = all checks passed.
Exit 1 = at least one check failed (details printed to stdout for the log).
"""

import asyncio
import os
import sys

# Match the env setup from tests/conftest.py so the app boots correctly.
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password-smoke")
os.environ.setdefault("APP_SECRET", "s" * 32)
os.environ.setdefault("SESSION_DAYS", "30")

import bcrypt
from httpx import AsyncClient, ASGITransport
from itsdangerous import URLSafeTimedSerializer

# App imports must come after env vars are set.
from app.main import app
from app.db import open_db, set_db, clear_db
from app.routes.workouts import set_http_client


def _session_token(user_id: int) -> str:
    s = URLSafeTimedSerializer(os.environ["APP_SECRET"], salt="fitstorm-session")
    return s.dumps({"user_id": user_id, "sid": "smoke-sid"})


# Each tuple: (path, text_that_must_appear, label)
CHECKS = [
    ("/",           "Dashboard",          "dashboard"),
    ("/workouts",   "Workouts",           "workouts"),
    ("/exercises",  "Exercises",          "exercises"),
    ("/metrics",    "Body Metrics",       "metrics"),
    ("/challenges", "Challenges",         "challenges"),
    ("/stats",      "Stats",              "stats"),
    ("/cardio",     "Cardio",             "cardio"),
    ("/prs",        "Personal Records",   "prs"),
    ("/settings",   "Settings",           "settings"),
    ("/journal",    "Daily Log",          "journal"),
]

# Match full HTML error patterns — not bare numbers (font-weight: 500 etc.)
ERROR_MARKERS = [
    "Internal Server Error",
    "Traceback (most recent call last)",
    "HTTP 500",
    ">500<",
    "raise HTTPException",
]


async def run() -> bool:
    conn = await open_db(":memory:")
    set_db(conn)
    set_http_client(None)

    # Seed a minimal user + session row.
    pw_hash = bcrypt.hashpw(b"smoke-pw", bcrypt.gensalt(rounds=4)).decode()
    await conn.execute(
        "INSERT INTO users(id, username, password_hash, is_admin) VALUES (1, 'smoke', ?, 0)",
        (pw_hash,),
    )
    await conn.execute(
        "INSERT OR REPLACE INTO sessions(id, user_id, expires_at) "
        "VALUES ('smoke-sid', 1, datetime('now','+1 day'))",
    )
    await conn.commit()

    token = _session_token(user_id=1)
    transport = ASGITransport(app=app)

    failures = []
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"fitstorm_session": token},
        follow_redirects=True,
    ) as client:
        for path, must_contain, label in CHECKS:
            try:
                r = await client.get(path, headers={"Accept": "text/html"})
            except Exception as exc:
                failures.append(f"  FAIL  {label}: exception — {exc}")
                continue

            if r.status_code != 200:
                failures.append(
                    f"  FAIL  {label}: HTTP {r.status_code} (expected 200)"
                )
                continue

            body = r.text
            if must_contain not in body:
                failures.append(
                    f"  FAIL  {label}: expected {must_contain!r} not found in response"
                )
                continue

            hit = next((m for m in ERROR_MARKERS if m in body), None)
            if hit:
                failures.append(
                    f"  FAIL  {label}: error marker {hit!r} found in rendered HTML"
                )
                continue

            print(f"  ok    {label}")

    await conn.close()
    clear_db()

    if failures:
        print("\nSmoke test FAILED:")
        for f in failures:
            print(f)
        return False

    print(f"\nSmoke test passed ({len(CHECKS)} pages OK)")
    return True


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
