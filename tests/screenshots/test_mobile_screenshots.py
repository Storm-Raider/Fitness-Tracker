"""
Mobile screenshot baseline capture for FitStorm.

Spins up a real uvicorn server against a temp SQLite DB, seeds a test user +
session, then takes 5 screenshots at 390×844 px (iPhone 14 portrait).

This test ONLY captures — it asserts nothing about image content.
Run it any time to refresh the baseline:

    pytest tests/screenshots/test_mobile_screenshots.py -v

Screenshots land in tests/snapshots/baseline/.
"""

import os
import socket
import threading
import time
import tempfile
from pathlib import Path

import bcrypt
import aiosqlite
import pytest
import uvicorn
from itsdangerous import URLSafeTimedSerializer
from playwright.sync_api import Page, BrowserContext

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIEWPORT = {"width": 390, "height": 844}  # iPhone 14 portrait
SNAPSHOT_DIR = Path(__file__).parent.parent / "snapshots" / "baseline"
COOKIE_NAME = "fitstorm_session"
_SESSION_SALT = "fitstorm-session"
_TEST_SID = "playwright-test-sid"
_TEST_USER_ID = 99  # use a high id to avoid clashing with normal test fixtures

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Grab an ephemeral port that is free at call time."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_session_token(user_id: int, sid: str, secret: str) -> str:
    return URLSafeTimedSerializer(secret, salt=_SESSION_SALT).dumps(
        {"user_id": user_id, "sid": sid}
    )


# ---------------------------------------------------------------------------
# Session-scoped live server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_server_url():
    """
    Start a real uvicorn server backed by a throw-away SQLite file, seed a
    test user + session, and yield the base URL.  Tears down after the session.
    """
    secret = "a" * 32  # must be ≥32 chars to satisfy app validation

    # Temp DB file so the app uses its own schema (not the in-memory test DB).
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    db_path = tmp_db.name

    port = _free_port()

    env_patch = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin-password-for-tests",
        "APP_SECRET": secret,
        "SESSION_DAYS": "30",
        "DATABASE_URL": db_path,  # some apps use this; set for safety
    }
    original_env = {}
    for k, v in env_patch.items():
        original_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Import app *after* env vars are set so lifespan picks them up.
    # We import here (not at module top) to ensure env is patched first.
    from app.main import app as fastapi_app  # noqa: PLC0415

    # Override the DATABASE_PATH that app.main resolved at import time.
    import app.main as _app_main  # noqa: PLC0415
    _app_main.DATABASE_PATH = db_path

    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to be accepting connections (max 10 s).
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError(f"Live server did not start on port {port}")

    # Seed test user + session directly into the SQLite file.
    import asyncio  # noqa: PLC0415

    async def _seed():
        async with aiosqlite.connect(db_path) as conn:
            pw_hash = bcrypt.hashpw(b"playwright-pw", bcrypt.gensalt(rounds=4)).decode()
            await conn.execute(
                "INSERT OR IGNORE INTO users(id, username, password_hash, is_admin) "
                "VALUES (?, ?, ?, 0)",
                (_TEST_USER_ID, "pw_testuser", pw_hash),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO sessions(id, user_id, expires_at) "
                "VALUES (?, ?, datetime('now','localtime','+30 days'))",
                (_TEST_SID, _TEST_USER_ID),
            )
            await conn.commit()

    asyncio.run(_seed())

    yield base_url

    # --- teardown ---
    server.should_exit = True
    thread.join(timeout=5)

    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    try:
        os.unlink(db_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Playwright context with auth cookie pre-loaded
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def authed_context(browser, live_server_url):
    """
    A Playwright browser context that already carries the session cookie,
    sized to the mobile viewport.
    """
    secret = "a" * 32
    token = _make_session_token(_TEST_USER_ID, _TEST_SID, secret)

    context: BrowserContext = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=2,  # retina-ish, matches iPhone 14
    )
    context.add_cookies(
        [
            {
                "name": COOKIE_NAME,
                "value": token,
                "domain": "127.0.0.1",
                "path": "/",
                "httpOnly": True,
                "sameSite": "Strict",
            }
        ]
    )
    yield context
    context.close()


@pytest.fixture()
def mobile_page(authed_context) -> Page:
    """Fresh page in the authenticated mobile context."""
    page = authed_context.new_page()
    yield page
    page.close()


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------


def _screenshot(page: Page, url: str, name: str, base_url: str) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    full_url = base_url.rstrip("/") + url
    page.goto(full_url, wait_until="networkidle", timeout=15_000)
    dest = SNAPSHOT_DIR / f"{name}-390.png"
    page.screenshot(path=str(dest), full_page=False)
    print(f"  saved {dest}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_screenshot_dashboard(mobile_page, live_server_url):
    _screenshot(mobile_page, "/", "dashboard", live_server_url)


def test_screenshot_workouts(mobile_page, live_server_url):
    _screenshot(mobile_page, "/workouts", "workouts", live_server_url)


def test_screenshot_plan(mobile_page, live_server_url):
    _screenshot(mobile_page, "/plan", "plan", live_server_url)


def test_screenshot_stats(mobile_page, live_server_url):
    _screenshot(mobile_page, "/stats", "stats", live_server_url)


def test_screenshot_exercises(mobile_page, live_server_url):
    _screenshot(mobile_page, "/exercises", "exercises", live_server_url)
