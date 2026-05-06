import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest_asyncio.fixture
async def anon_client(db_conn):
    """Unauthenticated client — no session cookie. Hits auth middleware directly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_login_page_renders(anon_client):
    resp = await anon_client.get("/login")
    assert resp.status_code == 200
    assert b"password" in resp.content.lower()


@pytest.mark.asyncio
async def test_login_success_redirects_and_sets_cookie(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"password": "test-password-for-ci", "next": "/"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "fittrack_session" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"password": "wrong-password", "next": "/"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_redirects_to_login(client):
    resp = await client.post("/logout")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_middleware_redirects_unauthenticated_request(anon_client):
    resp = await anon_client.get("/workouts")
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/login" in location
    assert "next=" in location


@pytest.mark.asyncio
async def test_open_redirect_prevention(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"password": "test-password-for-ci", "next": "http://evil.com"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
