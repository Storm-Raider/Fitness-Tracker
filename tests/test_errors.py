"""Regression tests for the global 404 handler's content negotiation.

Bug: app/main.py's @app.exception_handler(404) unconditionally rendered the
styled errors/404.html page, even for JSON API / HTMX requests, so a bogus
workout ID on PATCH/DELETE would return an HTML body instead of a parseable
{"detail": ...} JSON payload. Fixed by mirroring app.utils.render's
content-negotiation logic: real browser navigations (Accept: text/html, no
HX-Request) still get the styled HTML page; everything else gets JSON.
"""
import pytest


@pytest.mark.asyncio
async def test_json_api_404_returns_json_body_by_default(client):
    """A JSON API client (no explicit Accept: text/html) hitting a bogus
    workout ID should get a parseable JSON error body, not the HTML page."""
    resp = await client.patch("/workouts/99999", json={"notes": "ghost"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert "detail" in body


@pytest.mark.asyncio
async def test_json_api_404_with_explicit_accept_json(client):
    resp = await client.patch(
        "/workouts/99999",
        json={"notes": "ghost"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"]


@pytest.mark.asyncio
async def test_htmx_partial_404_returns_json_not_html(client):
    """HTMX partial requests (HX-Request header) should also get JSON, since
    there is no HTML partial to render for a 404."""
    resp = await client.patch(
        "/workouts/99999",
        json={"notes": "ghost"},
        headers={"HX-Request": "true", "Accept": "text/html"},
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"]


@pytest.mark.asyncio
async def test_browser_accept_header_still_gets_html_page(client):
    """A request that explicitly declares Accept: text/html (and isn't an
    HTMX request) should still get the styled HTML error page."""
    resp = await client.patch(
        "/workouts/99999",
        json={"notes": "ghost"},
        headers={"Accept": "text/html"},
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Not Found — Zenkai" in resp.text


@pytest.mark.asyncio
async def test_bogus_top_level_path_browser_navigation_renders_html(client):
    """A real browser navigating to a bogus top-level URL (address bar typo)
    must keep showing the nice styled error page."""
    resp = await client.get("/this-page-does-not-exist", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Not Found — Zenkai" in resp.text
