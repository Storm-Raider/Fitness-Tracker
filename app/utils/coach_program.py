"""
Deterministic programming for the AI coach.

Code decides what makes a routine a good programme: the split, the slots of each day
(main lift, secondary compound, accessories, isolation, core), the sets / reps / RIR /
rest of each slot for the goal, the loads (from the athlete's own recent e1RMs) and a
progression rule for every exercise. The model (app/routes/coach.py) only picks one
exercise per slot from a short ranked candidate list and writes the cues, so the plan's
quality no longer depends on how well a small model knows programming.

Everything here is pure: it takes library rows ({name, category, equipment,
primary_muscle}, see coach_plan.exercise_rows) and the training profile, and does no I/O.
"""

import math
import re
from collections import Counter

from app.utils.coach_plan import max_weekly_repeats, pain_avoid_pattern, resolve_name, staple_rank

# ── Movement patterns ────────────────────────────────────────────────

_PATTERN_NAMES: dict[str, tuple[str, ...]] = {
    "squat": ("Back Squat", "Front Squat", "Box Squat", "Pause Squat", "Hack Squat", "Leg Press",
              "Goblet Squat", "Zercher Squat", "Sumo Squat"),
    "hinge": ("Deadlift", "Romanian Deadlift", "Trap Bar Deadlift", "Sumo Deadlift", "Good Morning",
              "Rack Pull", "Cable Pull-Through", "Kettlebell Swing", "Single-Leg Romanian Deadlift",
              "Back Extension"),
    "single_leg": ("Bulgarian Split Squat", "Split Squat", "Lunge", "Reverse Lunge", "Walking Lunge",
                   "Step-up", "Lateral Lunge", "Pistol Squat"),
    "knee_flex": ("Leg Curl", "Seated Leg Curl", "Nordic Curl"),
    "knee_ext": ("Leg Extension", "Sissy Squat"),
    "glute": ("Hip Thrust", "Dumbbell Hip Thrust", "Glute Bridge", "Glute Kickback", "Frog Pump",
              "Donkey Kick", "Fire Hydrant", "Abductor Machine", "Adductor Machine"),
    "calf": ("Calf Raise", "Seated Calf Raise", "Leg Press Calf Raise"),
    "h_press": ("Bench Press", "Dumbbell Bench Press", "Machine Chest Press", "Decline Bench Press",
                "Decline Dumbbell Press", "Push-up", "Decline Push-up", "Dip", "Close-Grip Bench Press"),
    "incline_press": ("Incline Bench Press", "Incline Dumbbell Press", "Landmine Press", "Incline Push-up"),
    "v_press": ("Overhead Press", "Dumbbell Shoulder Press", "Arnold Press", "Machine Shoulder Press",
                "Push Press", "Pike Push-up"),
    "h_pull": ("Barbell Row", "Pendlay Row", "Cable Row", "Seated Cable Row", "Chest-Supported Row",
               "Dumbbell Row", "Machine Row", "Seal Row", "T-Bar Row", "Inverted Row",
               "Single-Arm Cable Row"),
    "v_pull": ("Pull-up", "Chin-up", "Neutral-Grip Pull-up", "Wide-Grip Pull-up", "Assisted Pull-up",
               "Lat Pulldown", "Close-Grip Lat Pulldown", "Wide-Grip Lat Pulldown",
               "Single-Arm Lat Pulldown", "Straight-Arm Pulldown", "Dumbbell Pullover"),
    "chest_iso": ("Cable Fly", "Dumbbell Fly", "Pec Deck"),
    "lat_delt": ("Lateral Raise", "Cable Lateral Raise", "Upright Row", "Front Raise"),
    "rear_delt": ("Face Pull", "Rear Delt Fly", "Cable Rear Delt Fly", "Band Pull-Apart"),
    "biceps": ("Barbell Curl", "Cable Curl", "Concentration Curl", "Dumbbell Curl", "EZ-Bar Curl",
               "Incline Dumbbell Curl", "Machine Preacher Curl", "Preacher Curl", "Spider Curl",
               "Hammer Curl", "Reverse Curl", "Zottman Curl"),
    "triceps": ("Tricep Pushdown", "Skull Crusher", "Overhead Tricep Extension",
                "Cable Overhead Tricep Extension", "Cable Tricep Kickback", "Dumbbell Tricep Kickback",
                "Diamond Push-up"),
    "core": ("Ab Crunch Machine", "Ab Wheel Rollout", "Bicycle Crunch", "Cable Crunch",
             "Captain's Chair Leg Raise", "Crunch", "Dead Bug", "Dragon Flag", "Flutter Kicks",
             "Hanging Knee Raise", "Hanging Leg Raise", "Hollow Hold", "Lying Leg Raise", "Pallof Press",
             "Plank", "Reverse Crunch", "Russian Twist", "Side Plank", "Sit-up", "Toes-to-Bar", "V-Up",
             "Woodchop", "L-Sit", "Copenhagen Plank", "Farmer's Carry"),
    # Never slotted (power, plyometric, conditioning, shrugs, grip). The model may still
    # pick one when the athlete asks for it.
    "other": ("Box Jump", "Jump Squat", "Burpee", "Power Clean", "Clean and Jerk", "Hang Clean",
              "Snatch", "Thruster", "Sled Push", "Medicine Ball Slam", "Wall Sit", "Mountain Climber",
              "Barbell Shrug", "Dumbbell Shrug", "Wrist Curl", "Turkish Get-up"),
}
PATTERN_OF: dict[str, str] = {n.lower(): p for p, names in _PATTERN_NAMES.items() for n in names}

# For exercises the athlete created themselves. Order matters: the first match wins.
_PATTERN_RX = [(re.compile(rx, re.I), p) for rx, p in (
    (r"calf", "calf"),
    (r"leg curl|hamstring curl|nordic", "knee_flex"),
    (r"leg extension", "knee_ext"),
    (r"hip thrust|glute|bridge|abduct|adduct", "glute"),
    (r"lunge|split squat|step[- ]?up|pistol", "single_leg"),
    (r"deadlift|\brdl\b|good morning|swing|pull[- ]?through|back extension|hyperextension", "hinge"),
    (r"squat|leg press", "squat"),
    (r"tricep|skull|pushdown", "triceps"),
    (r"incline.*(press|push)", "incline_press"),
    (r"overhead press|shoulder press|military|arnold|push press|pike", "v_press"),
    (r"bench|chest press|push[- ]?up|\bdips?\b|floor press", "h_press"),
    (r"pull[- ]?up|chin[- ]?up|pulldown|pullover", "v_pull"),
    (r"rear delt|face pull|pull[- ]?apart|reverse fly", "rear_delt"),
    (r"lateral raise|upright row|front raise", "lat_delt"),
    (r"\brow\b", "h_pull"),
    (r"\bfly|flye|pec deck|crossover", "chest_iso"),
    (r"curl", "biceps"),
    (r"kickback", "triceps"),
    (r"crunch|plank|leg raise|knee raise|sit[- ]?up|hollow|dead bug|pallof|woodchop|v-up|twist|carry|rollout", "core"),
)]

COMPOUND = frozenset({"squat", "hinge", "single_leg", "h_press", "incline_press", "v_press", "h_pull", "v_pull"})

# Coarse muscle each pattern trains, in the profile's vocabulary (undertrained, recovery).
PATTERN_MUSCLE = {
    "squat": "Legs", "hinge": "Legs", "single_leg": "Legs", "knee_flex": "Legs", "knee_ext": "Legs",
    "glute": "Legs", "calf": "Legs", "h_press": "Chest", "incline_press": "Chest", "chest_iso": "Chest",
    "v_press": "Shoulders", "lat_delt": "Shoulders", "rear_delt": "Shoulders", "h_pull": "Back",
    "v_pull": "Back", "biceps": "Biceps", "triceps": "Triceps", "core": "Abs",
}
# The isolation pattern that gives an under-trained muscle direct work.
_MUSCLE_PATTERN = {"Legs": "knee_flex", "Chest": "chest_iso", "Shoulders": "lat_delt", "Back": "h_pull",
                   "Biceps": "biceps", "Triceps": "triceps", "Abs": "core"}

_HOLDS = frozenset(n.lower() for n in ("Plank", "Side Plank", "Hollow Hold", "Wall Sit", "L-Sit",
                                       "Copenhagen Plank", "Farmer's Carry"))
# Lifts that belong in heavy, low-rep slots only: a 3×12 deadlift is a bad idea.
_HEAVY_ONLY = frozenset(n.lower() for n in (
    "Deadlift", "Sumo Deadlift", "Trap Bar Deadlift", "Rack Pull", "Back Squat", "Front Squat",
    "Box Squat", "Pause Squat", "Zercher Squat", "Push Press"))
# Fine as a variation, poor as a default: advanced, awkward or easy to get wrong. Ranked
# after the rest unless the athlete already does them.
_DEMOTED = frozenset(n.lower() for n in (
    "Nordic Curl", "Sissy Squat", "Pistol Squat", "Dragon Flag", "Toes-to-Bar", "L-Sit", "Upright Row",
    "Good Morning", "Rack Pull", "Zercher Squat", "Dumbbell Pullover", "Front Raise", "Donkey Kick",
    "Fire Hydrant", "Frog Pump", "Flutter Kicks", "Box Squat", "Pause Squat", "Sumo Squat",
    "Copenhagen Plank", "Pike Push-up", "Decline Push-up", "Close-Grip Bench Press", "Spider Curl",
    "Zottman Curl", "Reverse Curl", "Russian Twist", "Bicycle Crunch", "Woodchop", "Cable Pull-Through"))
_UNILATERAL = re.compile(r"single[- ]|split|lunge|step[- ]?up|pistol|dumbbell|concentration", re.I)


def pattern_of(name: str) -> str:
    key = (name or "").lower()
    if key in PATTERN_OF:
        return PATTERN_OF[key]
    for rx, pattern in _PATTERN_RX:
        if rx.search(key):
            return pattern
    return "other"


def is_hold(name: str) -> bool:
    return (name or "").lower() in _HOLDS


# ── Day templates and splits ─────────────────────────────────────────

def _s(role: str, patterns: str, prio: int) -> dict:
    """A slot: role, preferred patterns (first = preferred), prio (higher survives trimming)."""
    return {"role": role, "patterns": tuple(patterns.split()), "prio": prio}


TEMPLATES: dict[str, tuple[str, list[dict]]] = {
    "FULL_A": ("Full Body A", [_s("main", "squat", 10), _s("secondary", "h_press", 9), _s("secondary", "h_pull", 9),
                               _s("accessory", "hinge", 7), _s("accessory", "v_press incline_press", 6),
                               _s("isolation", "biceps", 4), _s("isolation", "triceps", 4), _s("core", "core", 5)]),
    "FULL_B": ("Full Body B", [_s("main", "hinge", 10), _s("secondary", "v_press incline_press", 9),
                               _s("secondary", "v_pull", 9), _s("accessory", "single_leg", 7),
                               _s("accessory", "h_press", 6), _s("isolation", "lat_delt", 5),
                               _s("isolation", "knee_flex", 4), _s("core", "core", 5)]),
    "FULL_C": ("Full Body C", [_s("main", "h_press", 10), _s("secondary", "squat", 9),
                               _s("secondary", "h_pull v_pull", 9), _s("accessory", "glute hinge", 7),
                               _s("accessory", "v_pull h_pull", 6), _s("isolation", "rear_delt", 5),
                               _s("isolation", "triceps", 4), _s("core", "core", 5)]),
    "UPPER_A": ("Upper A", [_s("main", "h_press", 10), _s("secondary", "h_pull", 9), _s("secondary", "v_press", 8),
                            _s("accessory", "v_pull", 8), _s("isolation", "lat_delt", 6), _s("isolation", "triceps", 5),
                            _s("isolation", "biceps", 5), _s("isolation", "chest_iso rear_delt", 3)]),
    "UPPER_B": ("Upper B", [_s("main", "v_press incline_press", 10), _s("secondary", "v_pull", 9),
                            _s("secondary", "incline_press h_press", 8), _s("accessory", "h_pull", 8),
                            _s("isolation", "rear_delt", 6), _s("isolation", "biceps", 5),
                            _s("isolation", "triceps", 5), _s("isolation", "lat_delt", 3)]),
    "LOWER_A": ("Lower A", [_s("main", "squat", 10), _s("secondary", "hinge", 9), _s("accessory", "single_leg", 8),
                            _s("isolation", "knee_flex", 7), _s("isolation", "calf", 5), _s("core", "core", 6),
                            _s("isolation", "knee_ext", 3)]),
    "LOWER_B": ("Lower B", [_s("main", "hinge", 10), _s("secondary", "squat", 9),
                            _s("accessory", "glute single_leg", 8), _s("isolation", "knee_flex", 7),
                            _s("isolation", "knee_ext", 6), _s("isolation", "calf", 5), _s("core", "core", 5)]),
    "PUSH_A": ("Push", [_s("main", "h_press", 10), _s("secondary", "v_press", 9), _s("accessory", "incline_press", 8),
                        _s("isolation", "lat_delt", 7), _s("isolation", "triceps", 6), _s("isolation", "chest_iso", 5),
                        _s("isolation", "triceps", 3)]),
    "PUSH_B": ("Push B", [_s("main", "v_press", 10), _s("secondary", "incline_press", 9), _s("accessory", "h_press", 8),
                          _s("isolation", "lat_delt", 7), _s("isolation", "triceps", 6), _s("isolation", "chest_iso", 5),
                          _s("isolation", "lat_delt", 3)]),
    "PULL_A": ("Pull", [_s("main", "h_pull", 10), _s("secondary", "v_pull", 9), _s("accessory", "h_pull", 7),
                        _s("isolation", "rear_delt", 7), _s("isolation", "biceps", 6), _s("core", "core", 5),
                        _s("isolation", "biceps", 4)]),
    "PULL_B": ("Pull B", [_s("main", "v_pull", 10), _s("secondary", "h_pull", 9), _s("accessory", "v_pull", 7),
                          _s("isolation", "rear_delt", 7), _s("isolation", "biceps", 6), _s("core", "core", 5),
                          _s("isolation", "biceps", 4)]),
    "LEGS_A": ("Legs", [_s("main", "squat", 10), _s("secondary", "hinge", 9), _s("accessory", "single_leg", 8),
                        _s("isolation", "knee_flex", 7), _s("isolation", "calf", 6), _s("isolation", "knee_ext", 5),
                        _s("core", "core", 4)]),
    "LEGS_B": ("Legs B", [_s("main", "hinge", 10), _s("secondary", "squat", 9), _s("accessory", "glute", 8),
                          _s("isolation", "knee_flex", 7), _s("isolation", "calf", 6), _s("accessory", "single_leg", 5),
                          _s("core", "core", 4)]),
    "RECOVERY": ("Recovery", [_s("accessory", "single_leg glute", 8), _s("accessory", "h_pull", 8),
                              _s("accessory", "incline_press h_press", 7), _s("core", "core", 7),
                              _s("isolation", "rear_delt", 6), _s("isolation", "lat_delt", 5),
                              _s("isolation", "biceps triceps", 4)]),
}

_DEFAULT_SPLITS = {
    1: ["FULL_A"],
    2: ["FULL_A", "FULL_B"],
    3: ["FULL_A", "FULL_B", "FULL_C"],
    4: ["UPPER_A", "LOWER_A", "UPPER_B", "LOWER_B"],
    5: ["UPPER_A", "LOWER_A", "PUSH_A", "PULL_A", "LEGS_A"],
    6: ["PUSH_A", "PULL_A", "LEGS_A", "PUSH_B", "PULL_B", "LEGS_B"],
    7: ["PUSH_A", "PULL_A", "LEGS_A", "PUSH_B", "PULL_B", "LEGS_B", "RECOVERY"],
}
# Splits the athlete can ask for by name, for the day counts they fit.
_REQUESTED_SPLITS = {
    "ppl": {3: ["PUSH_A", "PULL_A", "LEGS_A"], 6: _DEFAULT_SPLITS[6]},
    "upper_lower": {2: ["UPPER_A", "LOWER_A"], 4: _DEFAULT_SPLITS[4]},
    "full": {1: ["FULL_A"], 2: ["FULL_A", "FULL_B"], 3: _DEFAULT_SPLITS[3], 4: ["FULL_A", "FULL_B", "FULL_C", "FULL_A"]},
}
_SPLIT_WORDS = [
    (re.compile(r"\bppl\b|push[\s/,&-]*(?:and\s+)?pull[\s/,&-]*(?:and\s+)?legs?", re.I), "ppl"),
    (re.compile(r"upper[\s/,&-]*(?:and\s+)?lower", re.I), "upper_lower"),
    (re.compile(r"full[\s-]*body", re.I), "full"),
]

# When pain removes every candidate of a slot's patterns, try these instead (in order).
_FALLBACK_PATTERNS = {
    "squat": ("glute", "knee_flex", "hinge"), "single_leg": ("glute", "knee_flex"),
    "knee_ext": ("knee_flex", "glute"), "hinge": ("glute", "knee_flex", "h_pull"),
    "glute": ("knee_flex",), "v_press": ("incline_press", "lat_delt", "h_press"),
    "h_press": ("incline_press", "chest_iso"), "incline_press": ("h_press", "chest_iso"),
    "h_pull": ("v_pull",), "v_pull": ("h_pull",), "triceps": ("chest_iso",), "biceps": ("h_pull",),
    "lat_delt": ("rear_delt",), "chest_iso": ("h_press",), "knee_flex": ("glute",),
}


def requested_split(focus_note: str, days: int) -> list[str] | None:
    """The split the athlete named in their request, when it fits the day count."""
    for rx, key in _SPLIT_WORDS:
        if rx.search(focus_note or ""):
            return _REQUESTED_SPLITS[key].get(days)
    return None


# ── Goal schemes ─────────────────────────────────────────────────────

ROLES = ("main", "secondary", "accessory", "isolation", "core")

# goal -> role -> (sets, reps, RIR, rest seconds)
SCHEMES: dict[str, dict[str, tuple[int, str, int, int]]] = {
    "strength": {"main": (5, "3-5", 2, 180), "secondary": (4, "5-6", 2, 150), "accessory": (3, "8-10", 2, 90),
                 "isolation": (3, "10-12", 1, 60), "core": (3, "8-12", 2, 60)},
    "hypertrophy": {"main": (4, "6-8", 2, 150), "secondary": (3, "8-10", 2, 120), "accessory": (3, "10-12", 1, 90),
                    "isolation": (3, "12-15", 1, 60), "core": (3, "10-15", 1, 60)},
    "general": {"main": (3, "5-8", 2, 150), "secondary": (3, "8-10", 2, 120), "accessory": (3, "10-12", 2, 90),
                "isolation": (2, "12-15", 1, 60), "core": (2, "10-15", 2, 60)},
}
SCHEMES["balance"] = SCHEMES["hypertrophy"]
_HOLD_REPS = "30-45s"
_NEXT_ROLE = {"main": "secondary", "secondary": "accessory", "accessory": "isolation",
              "isolation": "accessory", "core": "core"}


def scheme(goal: str, role: str) -> dict:
    sets, reps, rir, rest = SCHEMES.get(goal, SCHEMES["general"])[role]
    return {"sets": sets, "reps": reps, "rir": rir, "rest_sec": rest}


_TIMED = re.compile(r"\d\s*(?:s|secs?|seconds?|min|mins|minutes?)\b")


def is_timed(reps: str) -> bool:
    """'30-45s', '60 sec', '1 min' are times; '8-12 reps' is not."""
    return bool(_TIMED.search(str(reps or "").lower()))


def top_reps(reps: str) -> int | None:
    """Top of a rep range ('3-5' -> 5, '8-12 reps' -> 12); None for a time ('30-45s') or
    text without a number ('AMRAP')."""
    if is_timed(reps):
        return None
    nums = [int(n) for n in re.findall(r"\d+", str(reps or ""))]
    return max(nums) if nums else None


# ── Loads and progression ────────────────────────────────────────────

def load_for(e1rm: float | None, reps: str, rir: int | None, factor: float = 1.0) -> float | None:
    """Working load for `reps` at `rir` reps in reserve, from the Epley e1RM the app uses
    everywhere (pr_utils.epley): the weight that leaves `rir` reps at the top of the range
    is e1RM / (1 + (top + rir) / 30). Rounded DOWN to 2.5 kg, so the target is never
    heavier than the maths says."""
    top = top_reps(reps)
    if not e1rm or top is None or rir is None:
        return None
    load = e1rm / (1 + (top + rir) / 30) * factor
    load = math.floor(load / 2.5) * 2.5
    return load if load > 0 else None


def progression_for(row: dict, pattern: str, reps: str, rir: int, *, loaded: bool, stalled: bool = False) -> str:
    name = row.get("name", "")
    if is_hold(name) or is_timed(reps):
        return "All sets steady → add 10 s per set."
    top = top_reps(reps)
    if top is None:
        return "Beat last week's total reps at the same load, then add load."
    if stalled:
        return f"Stalled 4+ weeks — work at {reps} this week, then retest your top set."
    equipment = row.get("equipment") or ""
    if equipment == "Bodyweight" and not loaded:
        return f"All sets at {top} reps → add a rep per set; past {top + 5}, add load or a harder variation."
    if equipment in ("Dumbbell", "Kettlebell"):
        return f"All sets at {top} reps with {rir}+ in reserve → go up one dumbbell."
    if pattern in ("squat", "hinge") and equipment in ("Barbell", "Machine"):
        return f"All sets at {top} reps with {rir}+ in reserve → add 5 kg (10 lb)."
    return f"All sets at {top} reps with {rir}+ in reserve → add 2.5 kg (5 lb)."


def _effort_factor(name: str, profile: dict) -> float:
    key = name.lower()
    if any(l["name"].lower() == key for l in profile.get("high_effort_lifts") or []):
        return 0.95
    if any(l["name"].lower() == key for l in profile.get("low_effort_lifts") or []):
        return 1.025
    return 1.0


def exercise_targets(row: dict, role: str, sets: int, reps: str, rir: int, rest_sec: int,
                     profile: dict, goal: str = "general", *, keep_reps: bool = False) -> dict:
    """The full target for one exercise in a slot: sets, reps, RIR, rest, load and the
    progression rule. A stalled lift moves to the next role's rep range and a hold gets
    time-based reps, unless `keep_reps` (an edit chose the reps: keep them)."""
    name = row["name"]
    goal_factor = 0.95 if profile.get("last_plan_feedback") == "too_hard" else 1.0
    stalled = name.lower() in {s.lower() for s in profile.get("stalled") or []}
    if not keep_reps and is_hold(name):
        reps = _HOLD_REPS
    elif not keep_reps and stalled and role != "core":
        reps = scheme(goal, _NEXT_ROLE[role])["reps"]
    if is_hold(name):
        rir = None                      # reps in reserve means nothing for a timed hold
    e1rm = (profile.get("recent_e1rm") or {}).get(name.lower())
    load = load_for(e1rm, reps, rir, goal_factor * _effort_factor(name, profile))
    pattern = pattern_of(name)
    return {
        "sets": sets, "reps": reps, "rir": rir, "rest_sec": rest_sec, "load_kg": load,
        "progression": progression_for(row, pattern, reps, rir, loaded=load is not None, stalled=stalled),
    }


# ── Skeleton ─────────────────────────────────────────────────────────

def _rank_key(row: dict, profile: dict, goal: str):
    key = row["name"].lower()
    used = set(profile.get("equipment_used") or [])
    equip_ok = not used or row["equipment"] in used or row["equipment"] == "Bodyweight"
    history = (profile.get("sets_by_exercise") or {}).get(key, 0)
    return (
        -history,
        key not in (profile.get("recent_e1rm") or {}),
        not equip_ok,
        key in _DEMOTED and not history,
        not (goal == "balance" and _UNILATERAL.search(key)),
        staple_rank(row),
        row["name"],
    )


def _volume_delta(profile: dict) -> int:
    """Set change for accessory/isolation slots from last-plan feedback and wellness."""
    delta = 0
    fb = profile.get("last_plan_feedback")
    if fb == "too_hard":
        delta -= 1
    elif fb == "too_easy":
        delta += 1
    w = profile.get("wellness") or {}
    sleep = w.get("avg_sleep_hrs")
    if (sleep is not None and sleep < 6.5) or (w.get("low_energy_days") or 0) >= 4:
        delta -= 1
    return max(-1, min(1, delta))


def _slots_per_day(profile: dict) -> int:
    asm = profile.get("avg_session_minutes")
    n = max(4, min(10, round(asm / 7))) if asm else 6
    if profile.get("last_plan_feedback") == "skipped_often":
        n -= 1
    return max(4, n)


def build_skeleton(rows: list[dict], profile: dict, goal: str, days: int, focus_note: str = "") -> list[dict]:
    """The week as days of slots. Each day: {"focus", "template", "slots"}; each slot:
    {role, patterns, pattern, muscle, sets, reps, rir, rest_sec, candidates, default}.
    `candidates` (up to 5 names) start with `default`, the pick the plan falls back to."""
    goal = goal if goal in SCHEMES else "general"
    keys = list(requested_split(focus_note, days) or _DEFAULT_SPLITS[max(1, min(7, days))])
    pain_rx = pain_avoid_pattern(profile, focus_note)

    by_pattern: dict[str, list[dict]] = {}
    for r in rows:
        if pain_rx and pain_rx.search(r["name"]):
            continue
        by_pattern.setdefault(pattern_of(r["name"]), []).append(r)
    for lst in by_pattern.values():
        lst.sort(key=lambda r: _rank_key(r, profile, goal))

    def resolve_patterns(slot: dict) -> tuple[str, ...]:
        for p in slot["patterns"]:
            if by_pattern.get(p):
                return slot["patterns"]
        for p in _FALLBACK_PATTERNS.get(slot["patterns"][0], ()):
            if by_pattern.get(p):
                return (p,)
        return ()

    # 1 — slots per day: trim by priority, keep template order.
    limit = _slots_per_day(profile)
    week: list[dict] = []
    for key in keys:
        focus, template = TEMPLATES[key]
        slots = []
        for s in template:
            pats = resolve_patterns(s)
            if not pats:
                continue                                  # pain ruled out the whole region
            role = s["role"]
            if role in ("main", "secondary") and pats[0] not in COMPOUND:
                role = "accessory"                        # a fallback isolation is never a main lift
            slots.append({**s, "role": role, "patterns": pats})
        keep = sorted(range(len(slots)), key=lambda i: -slots[i]["prio"])[:limit]
        week.append({"focus": focus, "template": key, "slots": [slots[i] for i in sorted(keep)]})

    # 2 — every under-trained muscle gets direct work somewhere in the week.
    under = [m for m in profile.get("undertrained") or [] if m in _MUSCLE_PATTERN]
    covered = {PATTERN_MUSCLE.get(s["patterns"][0]) for d in week for s in d["slots"]}
    for muscle in under:
        if muscle in covered or not by_pattern.get(_MUSCLE_PATTERN[muscle]):
            continue
        counts = Counter(PATTERN_MUSCLE.get(s["patterns"][0]) for d in week for s in d["slots"])
        best = None
        for d in week:
            for i, s in enumerate(d["slots"]):
                m = PATTERN_MUSCLE.get(s["patterns"][0])
                if s["role"] in ("isolation", "core") and counts[m] > 1 and m not in under:
                    if best is None or s["prio"] < best[0]["slots"][best[1]]["prio"]:
                        best = (d, i)
        new = {"role": "isolation", "patterns": (_MUSCLE_PATTERN[muscle],), "prio": 5}
        if best:
            best[0]["slots"][best[1]] = new
        else:
            min(week, key=lambda d: len(d["slots"]))["slots"].append(new)
        covered.add(muscle)

    # 3 — Day 1's main lift should not hit a muscle trained in the last day.
    fatigued = {m for m, s in (profile.get("muscle_recovery") or {}).items() if s == "fatigued"}
    if fatigued and len(week) > 1:
        for k in range(len(week)):
            first = week[k]["slots"][0] if week[k]["slots"] else None
            if first and PATTERN_MUSCLE.get(first["patterns"][0]) not in fatigued:
                week = week[k:] + week[:k]
                break

    # 4 — schemes, adjustments, candidates and default picks.
    delta = _volume_delta(profile)
    cap = max_weekly_repeats(days)
    used: Counter = Counter()
    for day in week:
        day_names: set[str] = set()
        day_patterns: set[str] = set()
        for slot in day["slots"]:
            role = slot["role"]
            heavy_ok = role in ("main", "secondary")
            pool = [r for p in slot["patterns"] for r in by_pattern.get(p, [])
                    if heavy_ok or r["name"].lower() not in _HEAVY_ONLY]
            # A slot open to several patterns prefers one the day has not covered yet
            # (Full Body C: a row after a pulldown, not two pulldowns).
            pool.sort(key=lambda r: (pattern_of(r["name"]) in day_patterns, _rank_key(r, profile, goal)))
            fresh = [r for r in pool if r["name"] not in day_names]
            # Main and secondary lifts repeat the athlete's best lift (up to the weekly cap):
            # practising the big lift twice a week is the point. Accessories rotate
            # variations, but only among the strong choices: an obscure fourth option is
            # worse than repeating a good accessory.
            history = profile.get("sets_by_exercise") or {}
            strong = [r for r in fresh[:3] if r["name"].lower() not in _DEMOTED or history.get(r["name"].lower())]
            if heavy_ok:
                strong = []
            default = (next((r for r in strong if used[r["name"]] == 0), None)
                       or next((r for r in fresh if used[r["name"]] < cap), None)
                       or (fresh[0] if fresh else None))
            if not default:
                slot["default"] = None
                continue
            pattern = pattern_of(default["name"])
            sch = scheme(goal, role)
            if day["template"] == "RECOVERY":
                sch = {**sch, "rir": 3}                 # an easy day: stop well short of failure
            if role == "main" and pattern in ("h_pull", "v_pull"):
                sch = {**scheme(goal, "secondary")}    # rows and pulldowns are not 3-5 rep lifts
            sets = sch["sets"]
            if role in ("accessory", "isolation"):
                sets = max(2, min(4, sets + delta))
            muscle = PATTERN_MUSCLE.get(pattern)
            if goal == "balance" and muscle in under:
                sets = min(5, sets + 1)
            used[default["name"]] += 1
            day_names.add(default["name"])
            day_patterns.add(pattern)
            others = [r["name"] for r in pool if r["name"] != default["name"]]
            slot.update(sch, sets=sets, pattern=pattern, muscle=muscle,
                        candidates=[default["name"]] + others[:4], default=default["name"])
        day["slots"] = [s for s in day["slots"] if s["default"]]
    return week


# ── Assembling the model's picks into a plan ─────────────────────────

def _fits_slot(name: str, role: str) -> bool:
    """Whether an exercise from outside a slot's candidates may fill it: heavy barbell
    lifts only in heavy slots, and only compounds in main/secondary slots."""
    if name.lower() in _HEAVY_ONLY and role not in ("main", "secondary"):
        return False
    return role not in ("main", "secondary") or pattern_of(name) in COMPOUND


def assemble_plan(skeleton: list[dict], raw: dict, goal: str, days: int,
                  name_map: dict, norm_map: dict, rows: list[dict], profile: dict,
                  focus_note: str = "", *, allow_outside: bool = False) -> tuple[dict, list[str]]:
    """Turn the model's picks into a plan. A pick is used when it is one of the slot's
    candidates (or, with `allow_outside` — the athlete's request or notes may ask for
    something else — any library exercise that fits the slot), is not pain-flagged, is not
    already on that day and stays within the weekly repeat cap. Otherwise the first
    candidate that satisfies the same rules stands and the name is reported in `dropped`.
    Targets always come from code. Candidates were filtered for pain and slot fit when
    the skeleton was built, so a fallback can never bring a flagged lift back."""
    row_of = {r["name"].lower(): r for r in rows}
    pain_rx = pain_avoid_pattern(profile, focus_note)
    cap = max_weekly_repeats(days)
    used: Counter = Counter()
    raw = raw if isinstance(raw, dict) else {}
    raw_days = [d for d in (raw.get("days") or []) if isinstance(d, dict)]
    dropped: list[str] = []
    out_days = []
    for di, day in enumerate(skeleton[:days]):
        picks = {}
        if di < len(raw_days):
            for p in raw_days[di].get("picks") or []:
                try:
                    picks.setdefault(int(p.get("slot")), p)
                except (TypeError, ValueError, AttributeError):
                    continue
        names_today: set[str] = set()
        exercises = []
        for si, slot in enumerate(day["slots"], start=1):
            pick = picks.get(si) or {}
            if not isinstance(pick, dict):
                pick = {}
            candidates = set(slot["candidates"])
            chosen, from_pick = None, False
            name = str(pick.get("name") or "").strip()
            if name:
                match = resolve_name(name, name_map, norm_map)
                ok = (match and match["name"].lower() in row_of
                      and (match["name"] in candidates or (allow_outside and _fits_slot(match["name"], slot["role"])))
                      and match["name"] not in names_today and used[match["name"]] < cap
                      and not (pain_rx and pain_rx.search(match["name"])))
                if ok:
                    chosen, from_pick = match, True
                elif name.lower() != (slot["default"] or "").lower():
                    dropped.append(name)
            if not chosen:
                default = (next((c for c in slot["candidates"] if c not in names_today and used[c] < cap), None)
                           or next((c for c in slot["candidates"] if c not in names_today), None))
                if not default:
                    continue
                chosen = name_map.get(default.lower())
                if not chosen:
                    continue
            used[chosen["name"]] += 1
            names_today.add(chosen["name"])
            row = row_of[chosen["name"].lower()]
            # The cue was written for the model's pick; it does not describe a default.
            cue = " ".join(str(pick.get("cue") or "").split())[:90] if from_pick else ""
            exercises.append({
                "exercise_id": chosen["id"], "name": chosen["name"], "note": cue,
                **exercise_targets(row, slot["role"], slot["sets"], slot["reps"], slot["rir"],
                                   slot["rest_sec"], profile, goal),
            })
        if exercises:
            out_days.append({"focus": day["focus"], "exercises": exercises})

    goal_word = {"strength": "Strength", "hypertrophy": "Hypertrophy", "balance": "Balance", "general": "General"}
    fallback_title = f"{days}-Day {goal_word.get(goal, 'Training')} Week"
    plan = {
        "title": " ".join(str(raw.get("title") or "").split())[:60] or fallback_title,
        "summary": " ".join(str(raw.get("summary") or "").split())[:200],
        "goal": goal,
        "days_per_week": days,
        "days": out_days,
    }
    return plan, dropped


# ── Keeping targets right after an edit (chat, swap, repair) ─────────

_TARGET_KEYS = ("rir", "rest_sec", "load_kg", "progression")


def infer_role(day: dict, idx: int) -> str:
    """The role an exercise plays from its pattern and position in the day: the first
    compound is the main lift, the next one secondary, later ones accessories; the rest
    are isolation (core patterns: core)."""
    exercises = day["exercises"]
    pattern = pattern_of(exercises[idx]["name"])
    if pattern == "core":
        return "core"
    if pattern not in COMPOUND:
        return "isolation"
    rank = sum(1 for e in exercises[:idx] if pattern_of(e["name"]) in COMPOUND)
    return ("main", "secondary")[rank] if rank < 2 else "accessory"


def annotate_targets(plan: dict, previous: dict | None, profile: dict, rows: list[dict],
                     only_days: "set[int] | list[int] | None" = None) -> dict:
    """Fill the target fields of the exercises in `plan` (in place; also returned). An
    exercise that sits on the same day of `previous` with the same id, sets and reps keeps
    its targets; anything else is recomputed from its own sets and reps. `only_days`
    (1-based) limits this to the days an edit changed, so untouched days stay as they were."""
    row_of = {r["name"].lower(): r for r in rows}
    goal = plan.get("goal") or "general"
    prev_days = (previous or {}).get("days") or []
    for di, day in enumerate(plan.get("days") or []):
        if only_days is not None and di + 1 not in only_days:
            continue
        old = {e["exercise_id"]: e for e in (prev_days[di]["exercises"] if di < len(prev_days) else [])}
        for idx, ex in enumerate(day["exercises"]):
            before = old.get(ex["exercise_id"])
            if (before and before.get("sets") == ex.get("sets") and before.get("reps") == ex.get("reps")
                    and all(k in before for k in _TARGET_KEYS)):
                for k in _TARGET_KEYS:
                    ex[k] = before[k]
                continue
            row = row_of.get(ex["name"].lower()) or {"name": ex["name"], "equipment": ""}
            role = infer_role(day, idx)
            sch = scheme(goal, role)
            t = exercise_targets(row, role, ex.get("sets") or sch["sets"], ex.get("reps") or sch["reps"],
                                 sch["rir"], sch["rest_sec"], profile, goal, keep_reps=True)
            for k in _TARGET_KEYS:
                ex[k] = t[k]
    return plan
