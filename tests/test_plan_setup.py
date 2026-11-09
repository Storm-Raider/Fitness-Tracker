"""The Plan page setup: equipment, session length and focus muscles (app/utils/plan_setup.py)
and how build_skeleton / assemble_plan / /coach/generate honour them."""

import pytest

from app.utils import coach_program as cp
from app.utils import plan_setup as ps
from tests.test_coach_program import NAME_MAP, ROWS, _profile

ROW = {r["name"]: r for r in ROWS}


def _week(goal="hypertrophy", days=4, **kw):
    return cp.build_skeleton(ROWS, _profile(**kw), goal, days)


def _all_candidates(week):
    return {c for d in week for s in d["slots"] for c in s["candidates"]}


# ── equipment ────────────────────────────────────────────────────────

def test_clean_equipment():
    assert ps.clean_equipment(None) is None
    assert ps.clean_equipment(list(ps.EQUIPMENT_KEYS)) is None        # everything = no limit
    assert ps.clean_equipment(["dumbbell", "bogus"]) == {"dumbbell"}
    assert ps.clean_equipment([]) == frozenset()                       # bodyweight only


@pytest.mark.parametrize("name,keys,ok", [
    ("Push-up", [], True),
    ("Pull-up", [], False), ("Pull-up", ["pullup_bar"], True),
    ("Dip", ["dumbbell"], False), ("Dip", ["dip_bars"], True),
    ("Dumbbell Bench Press", ["dumbbell"], False), ("Dumbbell Bench Press", ["dumbbell", "bench"], True),
    ("Goblet Squat", ["dumbbell"], True),
    ("Bench Press", ["bench", "dumbbell"], False),
    ("Lat Pulldown", ["dumbbell"], False), ("Lat Pulldown", ["machine"], False), ("Lat Pulldown", ["cable"], True),
])
def test_fits_equipment(name, keys, ok):
    assert ps.fits_equipment(ROW[name], frozenset(keys)) is ok


def test_bench_dips_need_a_bench_not_dip_bars():
    row = {"name": "Bench Dips", "equipment": "Bodyweight"}
    assert ps.fits_equipment(row, frozenset({"bench"}))
    assert not ps.fits_equipment(row, frozenset({"dip_bars"}))


def test_a_home_gym_week_uses_only_home_equipment():
    home = frozenset({"dumbbell", "bench", "pullup_bar", "band"})
    week = _week(equipment_available=home)
    names = _all_candidates(week)
    assert names
    assert all(ps.fits_equipment(ROW[n], home) for n in names), names
    assert not any(ROW[n]["equipment"] in ("Barbell", "Cable", "Machine") for n in names)
    assert all(len(d["slots"]) >= 3 for d in week)


def test_bodyweight_only_still_builds_a_week():
    week = _week(days=3, equipment_available=frozenset())
    assert all(ROW[n]["equipment"] == "Bodyweight" for n in _all_candidates(week))
    assert all(len(d["slots"]) >= 3 for d in week)


def test_outside_pick_with_unavailable_equipment_is_replaced():
    home = frozenset({"dumbbell", "bench"})
    profile = _profile(equipment_available=home)
    week = cp.build_skeleton(ROWS, profile, "hypertrophy", 2)
    raw = {"days": [{"picks": [{"slot": 1, "name": "Barbell Back Squat", "cue": "x"},
                               {"slot": 1, "name": "Back Squat", "cue": "x"}]}]}
    plan, dropped = cp.assemble_plan(week, raw, "hypertrophy", 2, NAME_MAP, {}, ROWS, profile,
                                     "add back squats", allow_outside=True)
    used = {e["name"] for d in plan["days"] for e in d["exercises"]}
    assert "Back Squat" not in used
    assert all(ps.fits_equipment(ROW[n], home) for n in used)


# ── session length ───────────────────────────────────────────────────

@pytest.mark.parametrize("minutes", [30, 45, 60, 90])
def test_days_fit_the_session_length(minutes):
    week = _week(goal="strength", days=3, session_minutes=minutes)
    plan, _ = cp.assemble_plan(week, {}, "strength", 3, NAME_MAP, {}, ROWS, _profile(session_minutes=minutes))
    for d in plan["days"]:
        n = len(d["exercises"])
        assert d["est_minutes"] <= minutes + 5 or n <= 3, (minutes, n, d["est_minutes"])
        assert n >= 3


def test_more_time_means_more_work():
    short = _week(days=3, session_minutes=30)
    long = _week(days=3, session_minutes=90)
    assert sum(len(d["slots"]) for d in long) > sum(len(d["slots"]) for d in short)


def test_main_lift_survives_a_short_session():
    for d in _week(goal="strength", days=4, session_minutes=30):
        assert any(s["role"] == "main" for s in d["slots"])


def test_day_minutes_estimate():
    ex = [{"sets": 4, "rest_sec": 120}, {"sets": 3, "rest_sec": 60}]
    # 5 warm-up + (4*40+3*120)/60+1.5 + (3*40+2*60)/60+1.5 = 5 + 10.17 + 5.5 = 20.7 -> 20
    assert ps.day_minutes(ex) == 20


# ── focus muscles ────────────────────────────────────────────────────

def _muscle_sets(week, patterns):
    return sum(s["sets"] for d in week for s in d["slots"] if s["pattern"] in patterns)


@pytest.mark.parametrize("muscle,patterns", [
    ("Chest", {"h_press", "incline_press", "chest_iso"}),
    ("Biceps", {"biceps"}),
    ("Glutes", {"glute"}),
    ("Shoulders", {"v_press", "lat_delt", "rear_delt"}),
])
def test_focus_adds_volume_for_that_muscle(muscle, patterns):
    base = _week(days=4)
    focused = _week(days=4, plan_focus=[muscle])
    assert _muscle_sets(focused, patterns) > _muscle_sets(base, patterns)
    marked = [s for d in focused for s in d["slots"] if s.get("focus") == muscle]
    assert 1 <= len(marked) <= ps.FOCUS_DAYS_CAP


def test_focus_goes_on_its_natural_days():
    # 6-day PPL: chest focus lands on the two push days, not on legs.
    week = _week(days=6, plan_focus=["Chest"])
    for d in week:
        if any(s.get("focus") == "Chest" for s in d["slots"]):
            assert d["focus"].lower().startswith("push")


def test_no_focus_slot_when_nothing_in_reach():
    # Dumbbells but no bench: every chest isolation/incline option needs a bench.
    week = _week(days=3, equipment_available=frozenset({"dumbbell"}), plan_focus=["Chest"])
    assert not any(s.get("focus") for d in week for s in d["slots"])


def test_45_minutes_is_a_real_session():
    for d in _week(days=3, session_minutes=45):
        assert len(d["slots"]) >= 4


def test_focus_survives_time_trimming():
    week = _week(days=4, session_minutes=45, plan_focus=["Biceps"])
    assert any(s.get("focus") == "Biceps" for d in week for s in d["slots"])


def test_clean_focus():
    assert ps.clean_focus(["Chest", "Chest", "Bogus", "Back", "Abs", "Legs"]) == ["Chest", "Back", "Abs", "Legs"]
    every = list(ps.FOCUS_MUSCLES)
    assert ps.clean_focus(every) == every              # no cap on how many


def test_many_focus_muscles_still_fit_the_session():
    every = list(ps.FOCUS_MUSCLES)
    week = _week(days=4, session_minutes=60, plan_focus=every)
    plan, _ = cp.assemble_plan(week, {}, "hypertrophy", 4, NAME_MAP, {}, ROWS, _profile(session_minutes=60))
    for d in plan["days"]:
        assert d["est_minutes"] <= 65
    marked = {s.get("focus") for d in week for s in d["slots"]} - {None}
    assert len(marked) >= 4


# ── the endpoint ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_rejects_a_bad_session_length(client):
    r = await client.post("/coach/generate", json={"goal": "strength", "days_per_week": 3, "session_minutes": 5})
    assert r.status_code == 422


def test_generate_in_cleans_the_setup():
    from app.routes.coach import GenerateIn, apply_setup, setup_json
    body = GenerateIn(goal="hypertrophy", days_per_week=3, equipment=["dumbbell", "bench", "x"],
                      session_minutes=45, focus_muscles=["Back", "Nope"])
    setup = body.setup()
    assert setup == {"equipment": frozenset({"dumbbell", "bench"}), "session_minutes": 45, "focus_muscles": ["Back"]}
    prof = apply_setup({"a": 1}, setup)
    assert prof["plan_focus"] == ["Back"] and prof["equipment_available"] == {"dumbbell", "bench"}
    stored = setup_json(setup)
    assert stored["equipment"] == ["bench", "dumbbell"]
    assert stored["equipment_labels"] == ["Dumbbells", "Bench"]
    assert GenerateIn(goal="general", days_per_week=2).setup()["equipment"] is None   # old clients


def test_prompt_states_the_setup(monkeypatch):
    import app.routes.coach as coach
    from app.routes.coach import _build_prompt, apply_setup
    monkeypatch.setattr(coach, "athlete_context", lambda profile, goal: [])
    profile = apply_setup(_profile(), {"equipment": frozenset({"dumbbell", "bench"}), "session_minutes": 45,
                                       "focus_muscles": ["Chest"]})
    week = cp.build_skeleton(ROWS, profile, "hypertrophy", 3)
    prompt = _build_prompt("hypertrophy", 3, profile, week, {}, "")
    assert "SESSION LENGTH: about 45 minutes" in prompt
    assert "EQUIPMENT THE ATHLETE HAS: Dumbbells, Bench" in prompt
    assert "FOCUS MUSCLES" in prompt and "FOCUS Chest" in prompt
    assert "Use only exercises the athlete's equipment allows." in prompt


# ── a whole generation with the setup (model faked) ──────────────────

@pytest.mark.asyncio
async def test_generation_honours_the_setup_and_remembers_it(client, db, monkeypatch):
    from app.routes import coach
    from tests.test_coach import _generate
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear()
    seen = {}

    async def pick(system, user, schema, **kwargs):
        seen["prompt"] = user
        # Try to sneak in a barbell lift; the setup has no barbell.
        return {"title": "Home Week", "summary": "s",
                "days": [{"picks": [{"slot": 1, "name": "Back Squat", "cue": "x"}]}]}
    monkeypatch.setattr(coach.gemini, "chat_json", pick)

    home = ["dumbbell", "bench", "pullup_bar", "band"]
    data = await _generate(client, "hypertrophy", 3, equipment=home, session_minutes=45,
                           focus_muscles=["Back"], focus_note="more back please")
    assert data["status"] == "done", data
    plan = data["plan"]
    assert plan["setup"] == {"equipment": sorted(home), "session_minutes": 45, "focus_muscles": ["Back"],
                             "equipment_labels": ["Dumbbells", "Bench", "Pull-up bar", "Bands"]}
    async with db.execute("SELECT name, equipment FROM exercises") as cur:
        row_of = {r["name"]: dict(r) for r in await cur.fetchall()}
    for d in plan["days"]:
        assert d["est_minutes"] <= 50
        for e in d["exercises"]:
            assert ps.fits_equipment(row_of[e["name"]], frozenset(home)), e["name"]
    assert "Back Squat" not in {e["name"] for d in plan["days"] for e in d["exercises"]}
    assert "FOCUS Back" in seen["prompt"]

    # The page opens with the same choices next time.
    ctx = (await client.get("/plan")).json()
    last = ctx["setup"]["last"]
    assert last["session_minutes"] == 45 and last["focus_muscles"] == ["Back"]
    assert last["equipment"] == sorted(home) and last["days_per_week"] == 3 and last["goal"] == "hypertrophy"
    assert [e["key"] for e in ctx["setup"]["equipment"]][:3] == ["barbell", "dumbbell", "kettlebell"]
    html = (await client.get("/plan", headers={"Accept": "text/html"})).text
    for key in ("barbell", "pullup_bar", "stability_ball"):
        assert f'data-eq="{key}"' in html
    assert html.count('class="eq-icon"') >= len(ps.EQUIPMENT)
    assert 'data-preset="home"' in html and 'data-muscle="Glutes"' in html and 'data-min="45"' in html
