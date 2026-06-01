import os

# Set auth env vars before importing app modules that read them at import time.
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password-for-tests")
os.environ.setdefault("APP_SECRET", "a" * 32)
os.environ.setdefault("SESSION_DAYS", "30")

import bcrypt
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from itsdangerous import URLSafeTimedSerializer

from app.main import app
from app.db import open_db, set_db, clear_db
from app.routes.workouts import set_http_client

TEST_USERNAME = "testuser"
TEST_PASSWORD = "test-password"


def _hash_test(password: str) -> str:
    # rounds=4 is the bcrypt minimum — fast for tests
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


import secrets as _secrets

def _test_session_token(user_id: int = 1, sid: str | None = None) -> str:
    secret = os.environ["APP_SECRET"]
    return URLSafeTimedSerializer(secret, salt="fitstorm-session").dumps(
        {"user_id": user_id, "sid": sid or "test-sid"}
    )


async def _seed_session(conn, user_id: int, sid: str = "test-sid") -> None:
    """Insert a server-side session row so the auth middleware accepts the cookie."""
    await conn.execute(
        "INSERT OR REPLACE INTO sessions(id, user_id, expires_at) "
        "VALUES (?, ?, datetime('now','localtime','+30 days'))",
        (sid, user_id),
    )
    await conn.commit()


@pytest_asyncio.fixture
async def db_conn():
    conn = await open_db(":memory:")
    set_db(conn)
    yield conn
    await conn.close()
    clear_db()


@pytest_asyncio.fixture
async def db(db_conn):
    """Alias so tests can request 'db' instead of 'db_conn'."""
    yield db_conn


@pytest_asyncio.fixture(autouse=True)
async def seed_test_user(db_conn):
    """Insert the primary test user (id=1) into every test's in-memory DB."""
    hashed = _hash_test(TEST_PASSWORD)
    await db_conn.execute(
        "INSERT INTO users(id, username, password_hash, is_admin) VALUES (1, ?, ?, 0)",
        (TEST_USERNAME, hashed),
    )
    await db_conn.commit()


@pytest_asyncio.fixture
async def client(db_conn, seed_test_user):
    await _seed_session(db_conn, user_id=1, sid="test-sid-1")
    set_http_client(None)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"fitstorm_session": _test_session_token(user_id=1, sid="test-sid-1")},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def anon_client(db_conn, seed_test_user):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_user(db_conn, seed_test_user):
    """Insert an admin user (id=3) for admin-only endpoint tests."""
    hashed = _hash_test("admin-password")
    await db_conn.execute(
        "INSERT INTO users(id, username, password_hash, is_admin) VALUES (3, 'adminuser', ?, 1)",
        (hashed,),
    )
    await db_conn.commit()
    return {"id": 3, "username": "adminuser"}


@pytest_asyncio.fixture
async def admin_client(db_conn, admin_user):
    await _seed_session(db_conn, user_id=3, sid="test-sid-3")
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"fitstorm_session": _test_session_token(user_id=3, sid="test-sid-3")},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def user_b(db_conn, seed_test_user):
    """Insert a second user (id=2) for isolation tests."""
    hashed = _hash_test("password-b")
    await db_conn.execute(
        "INSERT INTO users(id, username, password_hash, is_admin) VALUES (2, 'userb', ?, 0)",
        (hashed,),
    )
    await db_conn.commit()
    return {"id": 2, "username": "userb"}


@pytest_asyncio.fixture
async def user_b_client(db_conn, user_b):
    await _seed_session(db_conn, user_id=2, sid="test-sid-2")
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"fitstorm_session": _test_session_token(user_id=2, sid="test-sid-2")},
    ) as ac:
        yield ac
