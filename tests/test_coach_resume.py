"""Issue #28: leaving the Plan page mid-generation must not lose the request.

The job keeps running server-side, but its id lived only in the page's JS, so
coming back showed the empty state. The page now carries the user's in-flight
job id and re-attaches to it on load.
"""
import re

import pytest

from app.routes import coach


@pytest.fixture(autouse=True)
def _reset_coach_state():
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear(); coach._LAST_BY_USER.clear()
    yield
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear(); coach._LAST_BY_USER.clear()


def _active_job_id(html: str):
    m = re.search(r"const _activeJobId = (null|\"[0-9a-f]+\");", html)
    assert m, "plan page must declare _activeJobId"
    return None if m.group(1) == "null" else m.group(1).strip('"')


async def _plan_html(client):
    resp = await client.get("/plan", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    return resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "processing"])
async def test_plan_page_carries_the_users_in_flight_job(client, status):
    coach._JOBS["ab12"] = {"status": status, "user_id": 1}
    coach._ACTIVE_BY_USER[1] = "ab12"
    assert _active_job_id(await _plan_html(client)) == "ab12"


@pytest.mark.asyncio
async def test_no_job_when_nothing_is_running(client):
    assert _active_job_id(await _plan_html(client)) is None


@pytest.mark.asyncio
async def test_a_finished_job_is_not_resumed(client):
    # Its draft is already saved and the page renders that instead.
    coach._JOBS["cd34"] = {"status": "done", "user_id": 1, "plan": {}, "dropped": [], "model": "m"}
    coach._ACTIVE_BY_USER[1] = "cd34"
    assert _active_job_id(await _plan_html(client)) is None


@pytest.mark.asyncio
async def test_another_users_job_is_never_exposed(client, user_b):
    coach._JOBS["ef56"] = {"status": "processing", "user_id": 2}
    coach._ACTIVE_BY_USER[2] = "ef56"
    assert _active_job_id(await _plan_html(client)) is None
