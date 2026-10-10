"""TODO-EL-8: an HTMX request to any page built with render() must not 500.

render() answers HX-Request with {name}_partial.html, but five pages have no
partial, so an hx-get or hx-boost to them raised TemplateNotFound. Pages
without a partial now fall back to the full page.
"""
import pytest

# Every page whose route calls render(), and whether it ships a partial.
PAGES = [
    ("/", True),
    ("/workouts", True),
    ("/metrics", True),
    ("/achievements", False),
    ("/analytics", False),
    ("/cardio", False),
    ("/export", False),
    ("/plan", False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,has_partial", PAGES)
async def test_htmx_request_to_every_render_page(client, path, has_partial):
    resp = await client.get(path, headers={"HX-Request": "true", "Accept": "text/html"})
    assert resp.status_code == 200, resp.text[:200]
    is_full_page = "<html" in resp.text.lower()
    assert is_full_page is not has_partial


@pytest.mark.asyncio
async def test_htmx_invite_generation_returns_its_partial(admin_client):
    """POST /invite (the "Generate link" button) is the invite route that uses render()."""
    resp = await admin_client.post("/invite", data={"max_uses": 1}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "<html" not in resp.text.lower() and "/invite/accept/" in resp.text
