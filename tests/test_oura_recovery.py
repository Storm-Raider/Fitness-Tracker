"""Oura recovery for the coach and Analytics: the baseline maths, the profile and
prompt lines, the volume rule, and the Recovery chart."""
import json
import re
from datetime import date, timedelta

import pytest

from app.utils import coach_program as cp
from app.utils import oura_sync
from app.utils.coach_plan import athlete_context
from app.utils.training_profile import build_profile
from tests.test_coach_program import ROWS, _profile

TODAY = date(2026, 10, 11)
HTML = {"Accept": "text/html"}


def _days(n, today=TODAY, **vals):
    """n days ending today, newest first, each with the given values."""
    base = {"readiness_score": 80, "sleep_score": 78, "hrv_ms": 50.0, "resting_hr": 52.0}
    base.update(vals)
    return [{"day": (today - timedelta(days=i)).isoformat(), **base} for i in range(n)]


# ── Baseline maths ────────────────────────────────────────────────────

def test_recovery_averages_the_week_and_compares_with_the_month():
    # Last 7 days: HRV 40, RHR 55, readiness 65; the 23 days before: HRV 50, RHR 52.
    rows = _days(7, readiness_score=65, sleep_score=70, hrv_ms=40.0, resting_hr=55.0)
    rows += _days(23, today=TODAY - timedelta(days=7))
    r = oura_sync.summarise_recovery(rows, TODAY)
    assert r["today_readiness"] == 65
    assert r["avg_readiness_7d"] == 65 and r["avg_readiness_band"] == "Fair"
    assert r["avg_sleep_score_7d"] == 70
    # 30-day mean HRV = (7*40 + 23*50) / 30 = 47.67; 40 / 47.67 = -16 %
    assert r["hrv_vs_baseline_pct"] == -16
    # 30-day mean RHR = (7*55 + 23*52) / 30 = 52.7; 55 - 52.7 = +2.3 -> 2
    assert r["resting_hr_vs_baseline_bpm"] == 2


def test_recovery_needs_enough_days_for_each_number():
    # Only 5 days: averages yes, no baseline comparison (needs 14).
    r = oura_sync.summarise_recovery(_days(5), TODAY)
    assert r["avg_readiness_7d"] == 80
    assert r["hrv_vs_baseline_pct"] is None and r["resting_hr_vs_baseline_bpm"] is None
    # Two days: too few for a weekly average.
    r = oura_sync.summarise_recovery(_days(2), TODAY)
    assert r["avg_readiness_7d"] is None and r["today_readiness"] == 80
    # Nothing in the last week (ring left in a drawer): no recovery at all.
    assert oura_sync.summarise_recovery(_days(20, today=TODAY - timedelta(days=10)), TODAY) is None
    assert oura_sync.summarise_recovery([], TODAY) is None


def test_recovery_skips_missing_values_and_a_missing_today():
    rows = _days(30)[1:]                       # no row for today
    rows[0]["hrv_ms"] = None
    r = oura_sync.summarise_recovery(rows, TODAY)
    assert r["today_readiness"] is None
    assert r["hrv_vs_baseline_pct"] == 0


# ── Profile and prompt ───────────────────────────────────────────────

async def _put(db, rows, uid=1):
    for d in rows:
        await db.execute(
            "INSERT OR REPLACE INTO oura_daily(user_id, day, readiness_score, sleep_score, hrv_ms, resting_hr) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, d["day"], d["readiness_score"], d["sleep_score"], d["hrv_ms"], d["resting_hr"]),
        )
    await db.commit()


@pytest.mark.asyncio
async def test_profile_carries_recovery_only_for_its_own_user(db, user_b):
    profile = await build_profile(db, uid=1, fresh=True)
    assert profile["recovery"] is None
    await _put(db, _days(30, today=date.today()), uid=user_b["id"])
    assert (await build_profile(db, uid=1, fresh=True))["recovery"] is None
    await _put(db, _days(30, today=date.today(), readiness_score=88))
    rec = (await build_profile(db, uid=1, fresh=True))["recovery"]
    assert rec["today_readiness"] == 88 and rec["avg_readiness_7d"] == 88


@pytest.mark.asyncio
async def test_prompt_states_recovery_in_words(db):
    rows = _days(7, readiness_score=65, sleep_score=70, hrv_ms=40.0, resting_hr=55.0)
    rows += _days(23, today=TODAY - timedelta(days=7))
    prof = {**await build_profile(db, uid=1, fresh=True), "recovery": oura_sync.summarise_recovery(rows, TODAY)}
    text = "\n".join(athlete_context(prof, "hypertrophy"))
    line = next(l for l in text.splitlines() if "RECOVERY" in l)
    assert "today readiness 65 (Fair)" in line
    assert "7-day avg readiness 65 (Fair)" in line
    assert "avg sleep score 70" in line
    assert "HRV 16% below the 30-day baseline" in line
    assert "resting HR 2 bpm above the 30-day baseline" in line


@pytest.mark.asyncio
async def test_prompt_has_no_recovery_line_without_oura(db):
    prof = await build_profile(db, uid=1, fresh=True)
    assert "RECOVERY" not in "\n".join(athlete_context(prof, "hypertrophy"))


# ── Volume rule ──────────────────────────────────────────────────────

def _acc_sets(week):
    return [s["sets"] for d in week for s in d["slots"] if s["role"] in ("accessory", "isolation")]


def test_low_readiness_or_hrv_trims_accessory_volume_and_stays_clamped():
    base = _acc_sets(cp.build_skeleton(ROWS, _profile(), "hypertrophy", 4))
    hard = _acc_sets(cp.build_skeleton(ROWS, _profile(last_plan_feedback="too_hard"), "hypertrophy", 4))
    low = {"avg_readiness_7d": 66, "hrv_vs_baseline_pct": 0}
    hrv = {"avg_readiness_7d": 80, "hrv_vs_baseline_pct": -15}
    ok = {"avg_readiness_7d": 70, "hrv_vs_baseline_pct": -14}
    assert _acc_sets(cp.build_skeleton(ROWS, _profile(recovery=low), "hypertrophy", 4)) == hard
    assert _acc_sets(cp.build_skeleton(ROWS, _profile(recovery=hrv), "hypertrophy", 4)) == hard
    assert _acc_sets(cp.build_skeleton(ROWS, _profile(recovery=ok), "hypertrophy", 4)) == base
    # Poor sleep in the journal and low readiness don't stack past one set.
    both = _profile(recovery=low, wellness={"avg_sleep_hrs": 5.5}, last_plan_feedback="too_hard")
    assert _acc_sets(cp.build_skeleton(ROWS, both, "hypertrophy", 4)) == hard
    # Feeling it was too easy and a low week cancel out.
    mixed = _profile(recovery=low, last_plan_feedback="too_easy")
    assert _acc_sets(cp.build_skeleton(ROWS, mixed, "hypertrophy", 4)) == base


# ── Analytics ────────────────────────────────────────────────────────

def _recovery_json(page: str):
    m = re.search(r"const RECOVERY = (\[.*?\]);", page, re.S)
    return json.loads(m.group(1)) if m else None


@pytest.mark.asyncio
async def test_analytics_recovery_section_shows_weekly_averages(client, db):
    page = (await client.get("/analytics", headers=HTML)).text
    assert 'id="rec-chart"' not in page and _recovery_json(page) is None

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    # This week: readiness 70 and 90 -> 80. Five weeks ago: 60. Old data (20 weeks) is left out.
    await _put(db, [{"day": monday.isoformat(), "readiness_score": 70, "sleep_score": 75, "hrv_ms": 40.0, "resting_hr": 50.0},
                    {"day": (monday + timedelta(days=min(1, today.weekday()))).isoformat(), "readiness_score": 90,
                     "sleep_score": 85, "hrv_ms": 60.0, "resting_hr": 54.0},
                    {"day": (monday - timedelta(weeks=5)).isoformat(), "readiness_score": 60, "sleep_score": 65,
                     "hrv_ms": None, "resting_hr": 58.0},
                    {"day": (monday - timedelta(weeks=20)).isoformat(), "readiness_score": 10, "sleep_score": 10,
                     "hrv_ms": 10.0, "resting_hr": 90.0}])
    page = (await client.get("/analytics", headers=HTML)).text
    assert 'id="rec-chart"' in page and "Recovery (Oura)" in page
    for label in ("Readiness", "Sleep", "HRV", "Resting HR"):
        assert f">{label}</button>" in page
    weeks = _recovery_json(page)
    assert len(weeks) == 12 and weeks[-1]["week_start"] == monday.isoformat()
    if today.weekday() >= 1:
        assert weeks[-1]["readiness"] == 80 and weeks[-1]["hrv"] == 50 and weeks[-1]["rhr"] == 52
    assert weeks[-6]["readiness"] == 60 and weeks[-6]["hrv"] is None
    assert weeks[0]["readiness"] is None
    assert all(w["readiness"] != 10 for w in weeks)


@pytest.mark.asyncio
async def test_analytics_recovery_is_per_user(client, db, user_b):
    await _put(db, _days(10, today=date.today()), uid=user_b["id"])
    assert "const RECOVERY" not in (await client.get("/analytics", headers=HTML)).text
