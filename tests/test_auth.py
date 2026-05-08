import pytest


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success_redirects_and_sets_cookie(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"username": "testuser", "password": "test-password", "next": "/"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "fittrack_session" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"username": "testuser", "password": "wrong", "next": "/"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_returns_401(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"username": "nobody", "password": "test-password", "next": "/"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_missing_username_field_returns_422(anon_client):
    resp = await anon_client.post(
        "/login",
        data={"password": "test-password", "next": "/"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Admin authorization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_page_accessible_to_admin(admin_client):
    resp = await admin_client.get("/invite")
    assert resp.status_code == 200
    assert b"invite" in resp.content.lower()


@pytest.mark.asyncio
async def test_invite_create_forbidden_for_non_admin(client):
    resp = await client.post("/invite")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invite_page_redirects_unauthenticated(anon_client):
    resp = await anon_client.get("/invite")
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Invite accept flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_accept_page_renders_for_valid_token(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-valid', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    resp = await anon_client.get("/invite/accept/tok-valid")
    assert resp.status_code == 200
    assert b"Create" in resp.content


@pytest.mark.asyncio
async def test_invite_accept_creates_user_and_redirects(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-create', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    resp = await anon_client.post(
        "/invite/accept/tok-create",
        data={"username": "newuser", "password": "newpassword", "password_confirm": "newpassword"},
    )
    assert resp.status_code in (302, 303)
    assert "login" in resp.headers["location"]

    async with db_conn.execute("SELECT id FROM users WHERE username = 'newuser'") as cur:
        row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_invite_accept_expired_token_returns_400(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-expired', 1, datetime('now','localtime','-1 hour'))"
    )
    await db_conn.commit()

    resp = await anon_client.get("/invite/accept/tok-expired")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invite_accept_single_use_token(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-once', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    await anon_client.post(
        "/invite/accept/tok-once",
        data={"username": "firstuser", "password": "password1", "password_confirm": "password1"},
    )
    resp = await anon_client.get("/invite/accept/tok-once")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_accept_password_mismatch(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-mismatch', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    resp = await anon_client.post(
        "/invite/accept/tok-mismatch",
        data={"username": "user1", "password": "password1", "password_confirm": "differentpass"},
    )
    assert resp.status_code == 200
    assert b"Passwords do not match" in resp.content


@pytest.mark.asyncio
async def test_invite_accept_username_already_taken(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-dup', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    resp = await anon_client.post(
        "/invite/accept/tok-dup",
        data={"username": "testuser", "password": "newpassword", "password_confirm": "newpassword"},
    )
    assert resp.status_code == 200
    assert b"already taken" in resp.content.lower()


@pytest.mark.asyncio
async def test_invite_accept_invalid_username_format(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-badname', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    resp = await anon_client.post(
        "/invite/accept/tok-badname",
        data={"username": "bad name!", "password": "password1", "password_confirm": "password1"},
    )
    assert resp.status_code == 200
    assert b"Username must be" in resp.content


@pytest.mark.asyncio
async def test_invite_accept_password_too_short(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES ('tok-short', 1, datetime('now','localtime','+48 hours'))"
    )
    await db_conn.commit()

    resp = await anon_client.post(
        "/invite/accept/tok-short",
        data={"username": "newuser2", "password": "short", "password_confirm": "short"},
    )
    assert resp.status_code == 200
    assert b"8" in resp.content


# ---------------------------------------------------------------------------
# Invalid token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_accept_nonexistent_token_returns_400(anon_client):
    resp = await anon_client.get("/invite/accept/totallymadeuptoken")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

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
        data={"username": "testuser", "password": "test-password", "next": "http://evil.com"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    resp = await client.post("/logout")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
