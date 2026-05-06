import os

# Set auth env vars before importing app modules that read them at import time.
# These are test-only values; production must use real secrets from the environment.
os.environ.setdefault("APP_PASSWORD", "test-password-for-ci")
os.environ.setdefault("APP_SECRET", "a" * 32)
os.environ.setdefault("SESSION_DAYS", "30")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from itsdangerous import URLSafeTimedSerializer

from app.main import app
from app.db import open_db, set_db, clear_db
from app.routes.workouts import set_http_client


def _test_session_token() -> str:
    secret = os.environ["APP_SECRET"]
    return URLSafeTimedSerializer(secret, salt="fittrack-session").dumps("authenticated")


@pytest_asyncio.fixture
async def db_conn():
    conn = await open_db(":memory:")
    set_db(conn)
    yield conn
    await conn.close()
    clear_db()


@pytest_asyncio.fixture
async def client(db_conn):
    set_http_client(None)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"fittrack_session": _test_session_token()},
    ) as ac:
        yield ac
