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


def test_error_page_follows_the_design_system():
    """One template for 404 and 500, in the drafting style: no orange, 4px corners,
    Instrument Sans (the old pair of pages was still on the previous design)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    page = (root / "templates/errors/error.html").read_text()
    assert "#ff5a1f" not in page and "Archivo" not in page and "Instrument Sans" in page
    assert "border-radius: 4px" in page
    sw = (root / "static/sw.js").read_text()
    assert "#ff5a1f" not in sw and "Archivo" not in sw
    fonts = (root / "static/fonts/fonts.css").read_text()
    assert fonts.count("@font-face") == 1
    for url in __import__("re").findall(r"url\('([^']+)'\)", fonts):
        assert (root / "static/fonts" / url).is_file(), url


@pytest.mark.asyncio
async def test_server_error_renders_the_shared_page(client):
    from app.main import _server_error

    from starlette.requests import Request
    resp = await _server_error(Request({"type": "http", "method": "GET", "path": "/x", "headers": [],
                                        "query_string": b"", "server": ("test", 80), "scheme": "http"}), None)
    assert resp.status_code == 500
    body = resp.body.decode()
    assert "Server Error — Zenkai" in body and "Something went wrong" in body and "Error 500" in body
