"""Deterministic programming for the coach (app/utils/coach_program.py). Pure: library rows
come from the seed data and profiles are hand-built, so no database is needed."""

import pytest

from app.data.exercises import EXERCISES
from app.utils import coach_program as cp
from app.utils.coach_plan import max_weekly_repeats

ROWS = [
    {"name": e["name"], "category": e["category"], "equipment": e["equipment"],
     "primary_muscle": e["muscle_primary"].split(",")[0].strip()}
    for e in EXERCISES if e["category"] != "Cardio"
]
NAME_MAP = {r["name"].lower(): {"id": i + 1, "name": r["name"]} for i, r in enumerate(ROWS)}


def _profile(**kw) -> dict:
    base = {"total_workouts": 0, "undertrained": [], "muscle_recovery": {}, "stalled": [],
            "injury_flags": [], "recent_e1rm": {}, "sets_by_exercise": {}, "equipment_used": [],
            "high_effort_lifts": [], "low_effort_lifts": [], "wellness": {}, "avg_session_minutes": None,
            "last_plan_feedback": None}
    base.update(kw)
    return base


def _names(day):
    return [s["default"] for s in day["slots"]]


# ── Patterns ─────────────────────────────────────────────────────────

def test_every_seeded_exercise_has_a_pattern_in_the_table():
    missing = [r["name"] for r in ROWS if r["name"].lower() not in cp.PATTERN_OF]
    assert missing == []


@pytest.mark.parametrize("name,pattern", [
    ("Smith Machine Squat", "squat"), ("Hamstring Curl Machine", "knee_flex"),
    ("Landmine Row", "h_pull"), ("Cable Crossover", "chest_iso"), ("Glute Kickback Machine", "glute"),
    ("Tricep Dip Machine", "triceps"), ("Leg Press Calf Raise", "calf"), ("Mystery Move", "other"),
])
def test_regex_fallback_for_user_created_exercises(name, pattern):
    assert cp.pattern_of(name) == pattern


# ── Splits ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("days", range(1, 8))
@pytest.mark.parametrize("goal", ["strength", "hypertrophy", "balance", "general"])
def test_every_split_is_complete_and_varied(goal, days):
    week = cp.build_skeleton(ROWS, _profile(), goal, days)
    assert len(week) == days
    cap = max_weekly_repeats(days)
    counts = {}
    for day in week:
        names = _names(day)
        assert len(names) >= 4
        assert len(set(names)) == len(names)                  # never twice in a day
        for n in names:
            counts[n] = counts.get(n, 0) + 1
        if day["template"] != "RECOVERY":                     # the 7th day is all accessory work
            assert day["slots"][0]["role"] == "main"
    assert max(counts.values()) <= cap


def test_requested_split_is_honoured_when_it_fits():
    ppl = cp.build_skeleton(ROWS, _profile(), "hypertrophy", 3, "I'd like push pull legs please")
    assert [d["focus"] for d in ppl] == ["Push", "Pull", "Legs"]
    # 5 days has no named PPL template, so the default split stands.
    five = cp.build_skeleton(ROWS, _profile(), "hypertrophy", 5, "PPL")
    assert [d["template"] for d in five] == ["UPPER_A", "LOWER_A", "PUSH_A", "PULL_A", "LEGS_A"]


def test_strength_never_programs_isolation_or_accessories_below_six_reps():
    week = cp.build_skeleton(ROWS, _profile(), "strength", 5)
    for day in week:
        for s in day["slots"]:
            if s["role"] not in ("main", "secondary"):
                assert cp.top_reps(s["reps"]) is None or cp.top_reps(s["reps"]) >= 8, s
            else:
                assert s["pattern"] in cp.COMPOUND


# ── Personalisation ──────────────────────────────────────────────────

def test_candidates_put_the_athletes_own_lifts_first():
    prof = _profile(sets_by_exercise={"front squat": 40, "pendlay row": 30}, recent_e1rm={"front squat": 120.0})
    week = cp.build_skeleton(ROWS, prof, "strength", 4)
    lower_a = next(d for d in week if d["template"] == "LOWER_A")
    upper_a = next(d for d in week if d["template"] == "UPPER_A")
    assert lower_a["slots"][0]["default"] == "Front Squat"
    assert "Pendlay Row" in _names(upper_a)


def test_pain_removes_movements_and_falls_back():
    prof = _profile(injury_flags=[{"text": "left knee hurts on squats", "exercise": "Back Squat"}])
    week = cp.build_skeleton(ROWS, prof, "hypertrophy", 4)
    for day in week:
        for s in day["slots"]:
            for c in s["candidates"]:
                assert not any(w in c.lower() for w in ("squat", "lunge", "leg press", "leg extension", "step-up"))
    lower_a = next(d for d in week if d["template"] == "LOWER_A")
    assert len(lower_a["slots"]) >= 4
    assert lower_a["slots"][0]["role"] in ("main", "accessory")


def test_day_one_avoids_a_fatigued_muscle():
    prof = _profile(muscle_recovery={"Chest": "fatigued", "Back": "fatigued"})
    week = cp.build_skeleton(ROWS, prof, "general", 4)
    assert cp.PATTERN_MUSCLE[week[0]["slots"][0]["pattern"]] not in ("Chest", "Back")
    assert len(week) == 4


def test_session_length_trims_by_priority_and_keeps_the_main_lift():
    short = cp.build_skeleton(ROWS, _profile(avg_session_minutes=30), "hypertrophy", 4)
    long = cp.build_skeleton(ROWS, _profile(avg_session_minutes=70), "hypertrophy", 4)
    assert all(len(d["slots"]) == 4 for d in short)
    assert all(len(d["slots"]) > 4 for d in long)
    assert all(d["slots"][0]["role"] == "main" for d in short)


def test_feedback_and_wellness_change_accessory_volume():
    base = cp.build_skeleton(ROWS, _profile(), "hypertrophy", 4)
    hard = cp.build_skeleton(ROWS, _profile(last_plan_feedback="too_hard"), "hypertrophy", 4)
    tired = cp.build_skeleton(ROWS, _profile(wellness={"avg_sleep_hrs": 5.5}), "hypertrophy", 4)
    easy = cp.build_skeleton(ROWS, _profile(last_plan_feedback="too_easy"), "hypertrophy", 4)

    def acc_sets(week):
        return [s["sets"] for d in week for s in d["slots"] if s["role"] in ("accessory", "isolation")]

    assert all(h == b - 1 for h, b in zip(acc_sets(hard), acc_sets(base)))
    assert acc_sets(tired) == acc_sets(hard)
    assert all(e == min(4, b + 1) for e, b in zip(acc_sets(easy), acc_sets(base)))
    # Main lifts keep their volume.
    assert hard[0]["slots"][0]["sets"] == base[0]["slots"][0]["sets"]


def test_undertrained_muscle_gets_direct_work_even_in_short_sessions():
    prof = _profile(avg_session_minutes=28, undertrained=["Biceps"])
    week = cp.build_skeleton(ROWS, prof, "general", 2)
    assert any(s["pattern"] == "biceps" for d in week for s in d["slots"])


# ── Loads and progression ────────────────────────────────────────────

def test_load_formula_inverts_epley_and_rounds_down():
    # 100 kg e1RM, 5 reps + 2 in reserve -> 100 / (1 + 7/30) = 81.08 -> 80
    assert cp.load_for(100.0, "3-5", 2) == 80.0
    assert cp.load_for(100.0, "8-10", 2) == 70.0      # 100 / 1.4 = 71.4 -> 70
    assert cp.load_for(None, "3-5", 2) is None
    assert cp.load_for(100.0, "30-45s", 2) is None


def test_exercise_targets_use_recent_e1rm_effort_and_feedback():
    row = {"name": "Bench Press", "equipment": "Barbell"}
    prof = _profile(recent_e1rm={"bench press": 100.0})
    t = cp.exercise_targets(row, "main", 5, "3-5", 2, 180, prof, "strength")
    assert t == {"sets": 5, "reps": "3-5", "rir": 2, "rest_sec": 180, "load_kg": 80.0,
                 "progression": "All sets at 5 reps with 2+ in reserve → add 2.5 kg (5 lb)."}
    hot = _profile(recent_e1rm={"bench press": 100.0}, high_effort_lifts=[{"name": "Bench Press", "avg_rpe": 9}])
    assert cp.exercise_targets(row, "main", 5, "3-5", 2, 180, hot, "strength")["load_kg"] == 75.0


@pytest.mark.parametrize("row,reps,expected", [
    ({"name": "Back Squat", "equipment": "Barbell"}, "3-5", "add 5 kg (10 lb)"),
    ({"name": "Dumbbell Row", "equipment": "Dumbbell"}, "8-10", "go up one dumbbell"),
    ({"name": "Pull-up", "equipment": "Bodyweight"}, "6-8", "past 13, add load"),
    ({"name": "Plank", "equipment": "Bodyweight"}, "30-45s", "add 10 s per set"),
])
def test_progression_rules(row, reps, expected):
    assert expected in cp.progression_for(row, cp.pattern_of(row["name"]), reps, 2, loaded=False)


def test_stalled_lift_moves_rep_range_and_says_so():
    prof = _profile(stalled=["Bench Press"], recent_e1rm={"bench press": 100.0})
    t = cp.exercise_targets({"name": "Bench Press", "equipment": "Barbell"}, "main", 5, "3-5", 2, 180, prof, "strength")
    assert t["reps"] == "5-6" and t["progression"].startswith("Stalled")


def test_holds_get_time_reps():
    t = cp.exercise_targets({"name": "Plank", "equipment": "Bodyweight"}, "core", 3, "8-12", 2, 60, _profile())
    assert t["reps"] == "30-45s" and t["load_kg"] is None


# ── Assembling picks ─────────────────────────────────────────────────

def _skeleton():
    return cp.build_skeleton(ROWS, _profile(recent_e1rm={"back squat": 140.0}), "strength", 4)


def test_assemble_uses_valid_picks_and_defaults_the_rest():
    sk = _skeleton()
    lower = sk.index(next(d for d in sk if d["template"] == "LOWER_A"))
    slot1 = sk[lower]["slots"][0]
    alt = slot1["candidates"][1]
    raw = {"title": "Squat-Led Strength", "summary": "Built around your squat.",
           "days": [{"picks": []} for _ in sk]}
    raw["days"][lower]["picks"] = [
        {"slot": 1, "name": alt, "cue": "Brace hard, sit between your heels."},
        {"slot": 2, "name": "Totally Made Up Lift", "cue": "x"},
        {"slot": 3, "name": alt, "cue": "duplicate within the day"},
    ]
    plan, dropped = cp.assemble_plan(sk, raw, "strength", 4, NAME_MAP, {}, ROWS, _profile())
    day = plan["days"][lower]
    assert day["exercises"][0]["name"] == alt
    assert day["exercises"][0]["note"] == "Brace hard, sit between your heels."
    assert day["exercises"][1]["name"] == sk[lower]["slots"][1]["default"]
    assert day["exercises"][1]["note"] == ""
    assert len({e["name"] for e in day["exercises"]}) == len(day["exercises"])
    assert "Totally Made Up Lift" in dropped
    assert plan["title"] == "Squat-Led Strength"
    assert all(len(d["exercises"]) == len(s["slots"]) for d, s in zip(plan["days"], sk))
    for d in plan["days"]:
        for e in d["exercises"]:
            assert {"rir", "rest_sec", "load_kg", "progression"} <= set(e)


def test_assemble_rejects_a_pain_flagged_pick():
    prof = _profile(injury_flags=[{"text": "my knee hurts", "exercise": None}])
    sk = cp.build_skeleton(ROWS, prof, "general", 2)
    raw = {"days": [{"picks": [{"slot": 1, "name": "Back Squat", "cue": "go deep"}]}, {"picks": []}]}
    plan, dropped = cp.assemble_plan(sk, raw, "general", 2, NAME_MAP, {}, ROWS, prof)
    assert "Back Squat" in dropped
    assert all("squat" not in e["name"].lower() for d in plan["days"] for e in d["exercises"])


def test_assemble_survives_garbage_and_gives_a_title():
    sk = _skeleton()
    plan, dropped = cp.assemble_plan(sk, "nonsense", "strength", 4, NAME_MAP, {}, ROWS, _profile())
    assert plan["title"] == "4-Day Strength Week"
    assert len(plan["days"]) == 4 and dropped == []


# ── Targets after edits ──────────────────────────────────────────────

def test_annotate_keeps_unchanged_and_recomputes_changed():
    prof = _profile(recent_e1rm={"back squat": 140.0, "leg press": 300.0})
    sk = cp.build_skeleton(ROWS, prof, "strength", 2)
    plan, _ = cp.assemble_plan(sk, {}, "strength", 2, NAME_MAP, {}, ROWS, prof)
    import copy
    edited = copy.deepcopy(plan)
    first = edited["days"][0]["exercises"][0]
    first["progression"] = "kept"                          # marker: copied when unchanged
    plan_with_marker = copy.deepcopy(edited)
    second = edited["days"][0]["exercises"][1]
    second.update({"exercise_id": NAME_MAP["leg press"]["id"], "name": "Leg Press", "reps": "8-10"})
    for k in ("rir", "rest_sec", "load_kg", "progression"):
        second.pop(k, None)
    cp.annotate_targets(edited, plan_with_marker, prof, ROWS)
    assert edited["days"][0]["exercises"][0]["progression"] == "kept"
    lp = edited["days"][0]["exercises"][1]
    assert lp["load_kg"] == 212.5                           # 300 / (1 + 12/30) = 214.3 -> 212.5
    assert lp["rir"] == 2 and lp["progression"].endswith("add 5 kg (10 lb).")


def test_infer_role():
    day = {"exercises": [{"name": "Bench Press"}, {"name": "Barbell Row"}, {"name": "Lateral Raise"},
                         {"name": "Dumbbell Shoulder Press"}, {"name": "Plank"}]}
    assert [cp.infer_role(day, i) for i in range(5)] == ["main", "secondary", "isolation", "accessory", "core"]


@pytest.mark.parametrize("days", range(1, 8))
def test_heavy_lifts_only_in_heavy_slots_and_no_obscure_defaults(days):
    for goal in ("strength", "hypertrophy"):
        for day in cp.build_skeleton(ROWS, _profile(), goal, days):
            for s in day["slots"]:
                if s["role"] not in ("main", "secondary"):
                    assert s["default"].lower() not in cp._HEAVY_ONLY, (day["focus"], s)
                assert s["default"].lower() not in cp._DEMOTED, (day["focus"], s)


def test_multi_pattern_slot_prefers_a_pattern_the_day_lacks():
    prof = _profile(sets_by_exercise={"lat pulldown": 50, "chin-up": 40})
    full_c = cp.build_skeleton(ROWS, prof, "hypertrophy", 3)[2]
    pulls = [s["pattern"] for s in full_c["slots"] if s["pattern"] in ("h_pull", "v_pull")]
    assert sorted(pulls) == ["h_pull", "v_pull"]


def test_pulls_in_a_main_slot_use_the_secondary_scheme():
    pull = next(d for d in cp.build_skeleton(ROWS, _profile(), "strength", 6) if d["template"] == "PULL_A")
    assert pull["slots"][0]["reps"] == "5-6"
