"""What the Plan page renders for the coach chat in each state (the behaviour of the panel
itself is verified in a browser; see the PR4a test plan)."""
from pathlib import Path

import pytest

from app.utils.static_url import static_url
from tests.test_coach_chat_routes import seed_plan


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("COACH_CHAT_ENABLED", raising=False)


async def page(client):
    r = await client.get("/plan", headers={"Accept": "text/html"})
    assert r.status_code == 200
    return r.text


@pytest.mark.asyncio
async def test_the_panel_toggle_and_hashed_scripts_are_on_the_page(client, db):
    html = await page(client)
    assert 'id="coach-panel"' in html and 'id="coach-tabs"' in html
    assert 'id="ai-generate-panel"' in html
    for name in ("plan_view.js", "sheet.js", "coach_chat.js"):
        assert f'<script src="{static_url(name)}"></script>' in html
    # load order: the panel's scripts run before the page script that renders a draft
    assert html.index(static_url("coach_chat.js")) < html.index("const _initialDraftId")


@pytest.mark.asyncio
async def test_the_privacy_card_says_what_googles_terms_say(client):
    html = " ".join((await page(client)).split())
    assert "sent to Google's Gemini API" in html
    assert "human reviewers may read it" in html
    assert "don't send sensitive personal information" in html
    assert "removes it from Zenkai, not from Google" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["kill", "nokey"])
async def test_no_panel_and_no_chat_scripts_when_chat_is_off(client, monkeypatch, switch):
    if switch == "kill":
        monkeypatch.setenv("COACH_CHAT_ENABLED", "false")
    else:
        monkeypatch.delenv("GEMINI_API_KEY")
    html = await page(client)
    assert 'id="coach-panel"' not in html and 'id="coach-tabs"' not in html
    assert "/static/coach_chat.js" not in html and "/static/sheet.js" not in html   # (base.html mentions sheet.js in a CSS comment)
    assert 'id="ai-generate-panel"' in html                 # generating is unaffected
    assert "CoachChat.openSaved" not in html


@pytest.mark.asyncio
async def test_saved_ai_plans_get_a_coach_action(client, db):
    pid, _ = await seed_plan(db, status="saved")
    html = await page(client)
    assert f"CoachChat.openSaved({pid})" in html


@pytest.mark.asyncio
async def test_the_draft_hands_its_id_to_plan_state_as_plan_id(client, db):
    pid, _ = await seed_plan(db, rev=4)
    html = await page(client)
    assert f"PlanState.set({{plan: _draft, planId: _initialDraftId, draftId: _initialDraftId, rev: 4" in html
    assert f"const _initialDraftId = {pid};" in html


@pytest.mark.asyncio
async def test_the_stale_on_device_wording_is_gone(client):
    html = await page(client)
    assert "Runs on-device" not in html and "Written by Google Gemini" in html


def test_a_proposed_note_does_not_end_in_a_doubled_full_stop():
    # Live run: the model proposed "Lower back gets tight with Romanian deadlifts."
    # and the chip read "Remember: …deadlifts.?"
    js = (Path(__file__).resolve().parent.parent / "app/static/coach_chat.js").read_text()
    assert "chip.text.replace(/[\\s.!?]+$/, '')" in js
