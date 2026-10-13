"""After a plan is lighter for recovery, the next one says whether the athlete
bounced back: the volume note in the plan's header, the Plan page and the prompt."""
import json
import re
from datetime import date, timedelta

import pytest

from app.routes import coach
from app.utils import coach_program as cp
from app.utils.training_profile import build_profile, invalidate_profile
from tests.test_coach import _fake_chat, _generate, _real_exercise_names
from tests.test_coach_program import _profile

HTML = {"Accept": "text/html"}
LOW = {"avg_readiness_7d": 64, "avg_readiness_band": "Fair", "hrv_vs_baseline_pct": -5}
GOOD = {"avg_readiness_7d": 78, "avg_readiness_band": "Good", "hrv_vs_baseline_pct": 2}
TRIM = {"reasons": ["a 7-day readiness of 64 (Fair)"], "readiness_7d": 64, "date": "2026-10-05"}


# ── The note and what a plan records ───────────────────────────────────

def test_no_note_when_recovery_is_fine_and_the_last_plan_was_not_lighter():
    assert cp.volume_note(_profile(recovery=GOOD)) is None
    assert cp.recovery_trim(_profile(recovery=GOOD)) is None


def test_a_lighter_plan_says_why_and_records_it():
    p = _profile(recovery=LOW)
    assert cp.volume_note(p) == ("One set fewer on accessory work for recovery, "
                                 "because of a 7-day readiness of 64 (Fair).")
    assert cp.recovery_trim(p) == {"reasons": ["a 7-day readiness of 64 (Fair)"], "readiness_7d": 64}


def test_too_easy_feedback_cancels_the_cut_and_nothing_is_recorded():
    p = _profile(recovery=LOW, last_plan_feedback="too_easy")
    assert cp.volume_note(p) == ("Accessory work keeps its usual sets: you rated your last plan too easy, "
                                 "which outweighs a 7-day readiness of 64 (Fair).")
    assert cp.recovery_trim(p) is None


def test_still_run_down_after_a_lighter_plan():
    p = _profile(recovery={**LOW, "avg_readiness_7d": 66}, last_plan_trim=TRIM)
    assert cp.volume_note(p) == ("Still one set fewer on accessory work for recovery, "
                                 "because of a 7-day readiness of 66 (Fair). "
                                 "Your 7-day readiness was 64 at your last plan.")


def test_readiness_back_up_after_a_lighter_plan():
    p = _profile(recovery=GOOD, last_plan_trim=TRIM)
    assert cp.volume_note(p) == ("Back to full accessory volume: your 7-day readiness is up "
                                 "from 64 to 78 since your last plan.")
    assert cp.recovery_trim(p) is None


def test_journal_reason_cleared_after_a_lighter_plan():
    trim = {"reasons": ["5.8 h average sleep in your daily log"], "readiness_7d": None, "date": "2026-10-05"}
    p = _profile(wellness={"avg_sleep_hrs": 7.4}, last_plan_trim=trim)
    assert cp.volume_note(p) == ("Back to full accessory volume: your last plan was lighter because of "
                                 "5.8 h average sleep in your daily log, and that has cleared.")


def test_recovered_but_rated_too_hard():
    p = _profile(recovery=GOOD, last_plan_trim=TRIM, last_plan_feedback="too_hard")
    assert cp.volume_note(p) == ("Recovered since your last plan, but accessory work still loses a set "
                                 "because you rated that plan too hard.")


# ── The profile reads the last saved plan ──────────────────────────────

async def _plan_row(db, plan: dict, status: str | None = "saved", created: str = "2026-10-05 08:00:00"):
    await db.execute(
        "INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, status, created_at) "
        "VALUES (1, 'P', 'general', 3, ?, ?, ?)",
        (json.dumps(plan), status, created),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_profile_reads_the_trim_from_the_last_saved_plan(db):
    assert (await build_profile(db, 1, fresh=True))["last_plan_trim"] is None
    await _plan_row(db, {"days": [], "recovery_trim": {"reasons": ["x"], "readiness_7d": 64}})
    p = await build_profile(db, 1, fresh=True)
    assert p["last_plan_trim"] == {"reasons": ["x"], "readiness_7d": 64, "date": "2026-10-05"}
    # A draft is not a plan the athlete trained on; a newer saved plan without a trim clears it.
    await _plan_row(db, {"days": []}, status="draft", created="2026-10-06 08:00:00")
    assert (await build_profile(db, 1, fresh=True))["last_plan_trim"]["readiness_7d"] == 64
    await _plan_row(db, {"days": []}, created="2026-10-07 08:00:00")
    assert (await build_profile(db, 1, fresh=True))["last_plan_trim"] is None


@pytest.mark.asyncio
async def test_profile_ignores_a_malformed_trim(db):
    await _plan_row(db, {"days": [], "recovery_trim": "nope"})
    assert (await build_profile(db, 1, fresh=True))["last_plan_trim"] is None


# ── Generation, prompt and Plan page ───────────────────────────────────

async def _oura(db, readiness: int, days: int = 10):
    for i in range(days):
        await db.execute(
            "INSERT OR REPLACE INTO oura_daily(user_id, day, readiness_score) VALUES (1, ?, ?)",
            ((date.today() - timedelta(days=i)).isoformat(), readiness),
        )
    await db.commit()


@pytest.mark.asyncio
async def test_generated_plan_carries_the_note_and_the_trim(client, db, monkeypatch):
    seen = {}
    names = await _real_exercise_names(db, 1)

    async def fake(system, user, schema, **kw):
        seen["prompt"] = user
        return {"title": "T", "summary": "S", "days": [{"picks": [{"slot": 1, "name": names[0], "cue": ""}]}]}
    monkeypatch.setattr(coach.gemini, "chat_json", fake)
    await _oura(db, 62)
    data = await _generate(client, "general", 1)
    assert data["status"] == "done", data
    plan = data["plan"]
    assert plan["volume_note"].startswith("One set fewer on accessory work for recovery")
    assert plan["recovery_trim"]["readiness_7d"] == 62
    assert "- RECOVERY AND VOLUME (accessory sets are set in code): One set fewer" in seen["prompt"]
    async with db.execute("SELECT plan_json FROM coach_plans WHERE user_id = 1 AND status = 'draft'") as cur:
        stored = json.loads((await cur.fetchone())["plan_json"])
    assert stored["recovery_trim"]["readiness_7d"] == 62


@pytest.mark.asyncio
async def test_a_fine_week_adds_nothing_to_the_plan(client, db, monkeypatch):
    names = await _real_exercise_names(db, 1)
    monkeypatch.setattr(coach.gemini, "chat_json", _fake_chat(
        {"title": "T", "summary": "S", "days": [{"picks": [{"slot": 1, "name": names[0], "cue": ""}]}]}))
    await _oura(db, 82)
    plan = (await _generate(client, "general", 1))["plan"]
    assert "volume_note" not in plan and "recovery_trim" not in plan


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page))


@pytest.mark.asyncio
async def test_plan_page_says_the_athlete_bounced_back(client, db, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    await _plan_row(db, {"days": [], "recovery_trim": {"reasons": ["a 7-day readiness of 64 (Fair)"], "readiness_7d": 64}})
    await _oura(db, 80)
    invalidate_profile(1)
    text = _text((await client.get("/plan", headers=HTML)).text)
    assert "Next plan: back to full accessory volume: your 7-day readiness is up from 64 to 80 since your last plan." in text


@pytest.mark.asyncio
async def test_saving_a_plan_refreshes_the_plan_page(client, db, monkeypatch):
    """The profile is cached for 30 minutes; confirming a plan must not leave the
    page describing the plan before it."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    names = await _real_exercise_names(db, 1)
    monkeypatch.setattr(coach.gemini, "chat_json", _fake_chat(
        {"title": "T", "summary": "S", "days": [{"picks": [{"slot": 1, "name": names[0], "cue": ""}]}]}))
    await _oura(db, 62)
    data = await _generate(client, "general", 1)
    await client.get("/plan", headers=HTML)                     # caches the profile
    r = await client.post(f"/coach/plans/{data['draft_id']}/confirm", json={})
    assert r.status_code == 201, r.text
    text = _text((await client.get("/plan", headers=HTML)).text)
    assert "Next plan: still one set fewer on accessory work for recovery" in text


@pytest.mark.asyncio
async def test_a_plan_saved_without_a_draft_keeps_its_note_and_trim(client, db):
    names = await _real_exercise_names(db, 1)
    r = await client.post("/coach/save", json={
        "title": "T", "goal": "general", "days_per_week": 1,
        "days": [{"focus": "A", "exercises": [{"name": names[0], "sets": 3, "reps": "8-10"}]}],
        "volume_note": "One set fewer on accessory work for recovery, because of x.",
        "recovery_trim": {"reasons": ["x"], "readiness_7d": 61},
    })
    assert r.status_code == 201, r.text
    p = await build_profile(db, 1)
    assert p["last_plan_trim"]["readiness_7d"] == 61
