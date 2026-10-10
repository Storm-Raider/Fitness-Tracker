"""A generation that fails while you're on another page is reported when you come back.

Follow-up to #28: the error lived only in server memory, so the Plan page just
showed its empty state. It now shows the error once, unless the page that
started the job already received it.
"""
import re

import pytest

from app.routes import coach

ERR = "Coach is resting until tomorrow (daily limit reached)."


@pytest.fixture(autouse=True)
def _reset_coach_state():
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear(); coach._LAST_BY_USER.clear()
    yield
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear(); coach._LAST_BY_USER.clear()


def _shown_error(html: str):
    m = re.search(r'<div id="ai-result-error" class="flash flash-error"( style="display:none;")?>(.*?)</div>', html, re.S)
    assert m, "plan page must render #ai-result-error"
    return None if m.group(1) else m.group(2).strip()


async def _plan(client):
    return (await client.get("/plan", headers={"Accept": "text/html"})).text


def _failed_job(uid=1, job_id="f00d"):
    coach._JOBS[job_id] = {"status": "error", "user_id": uid, "error": ERR}
    coach._LAST_BY_USER[uid] = job_id


@pytest.mark.asyncio
async def test_an_unseen_failure_is_shown_once(client):
    _failed_job()
    first = _shown_error(await _plan(client))
    assert first and ERR in first and "last plan request" in first.lower()
    assert _shown_error(await _plan(client)) is None          # once, not on every visit


@pytest.mark.asyncio
async def test_a_failure_already_delivered_to_the_page_is_not_repeated(client):
    _failed_job()
    status = await client.get("/coach/generate/f00d")          # the page that started it polled the error
    assert status.json()["status"] == "error"
    assert _shown_error(await _plan(client)) is None


@pytest.mark.asyncio
async def test_a_successful_job_shows_no_error(client):
    coach._JOBS["beef"] = {"status": "done", "user_id": 1, "plan": {}, "dropped": [], "model": "m"}
    coach._LAST_BY_USER[1] = "beef"
    assert _shown_error(await _plan(client)) is None


@pytest.mark.asyncio
async def test_another_users_failure_is_not_shown(client, user_b):
    _failed_job(uid=2)
    assert _shown_error(await _plan(client)) is None


@pytest.mark.asyncio
async def test_the_error_text_is_escaped(client):
    coach._JOBS["bad1"] = {"status": "error", "user_id": 1, "error": "<img src=x onerror=alert(1)>"}
    coach._LAST_BY_USER[1] = "bad1"
    html = await _plan(client)
    assert "<img src=x" not in html and "&lt;img src=x" in html
