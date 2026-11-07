"""What the athlete tells the plan generator before it builds a week: the equipment
they have, how long a session can be and which muscles to bring up.

Pure: no I/O. The Plan page draws its controls from the same lists (EQUIPMENT,
SESSION_MINUTES, FOCUS_MUSCLES), coach_program.build_skeleton honours them, and
assemble_plan / the swap sheet never bring back an exercise the athlete can't do.
"""

import math
import re

# Key -> label and the library `equipment` values it unlocks. Order = the order on the page.
EQUIPMENT: list[dict] = [
    {"key": "barbell", "label": "Barbell", "values": ("Barbell",)},
    {"key": "dumbbell", "label": "Dumbbells", "values": ("Dumbbell",)},
    {"key": "kettlebell", "label": "Kettlebells", "values": ("Kettlebell",)},
    {"key": "bench", "label": "Bench", "values": ()},
    {"key": "pullup_bar", "label": "Pull-up bar", "values": ()},
    {"key": "dip_bars", "label": "Dip bars", "values": ()},
    {"key": "cable", "label": "Cable", "values": ("Cable",)},
    {"key": "machine", "label": "Machines", "values": ("Machine",)},
    {"key": "ez_bar", "label": "EZ bar", "values": ("EZ Bar",)},
    {"key": "band", "label": "Bands", "values": ("Band",)},
    {"key": "medicine_ball", "label": "Medicine ball", "values": ("Medicine Ball",)},
    {"key": "stability_ball", "label": "Stability ball", "values": ("Exercise Ball",)},
]
EQUIPMENT_KEYS = frozenset(e["key"] for e in EQUIPMENT)
_LABEL = {e["key"]: e["label"] for e in EQUIPMENT}
_VALUES = {e["key"]: e["values"] for e in EQUIPMENT}

# Quick choices above the grid.
PRESETS: list[dict] = [
    {"key": "gym", "label": "Full gym", "items": sorted(EQUIPMENT_KEYS)},
    {"key": "home", "label": "Home gym", "items": ["dumbbell", "bench", "pullup_bar", "band"]},
    {"key": "none", "label": "Bodyweight only", "items": []},
]

SESSION_MINUTES = (30, 45, 60, 75, 90)

# Focus muscle -> (patterns a focus slot draws from, its role, patterns that make a day
# a natural home for it; None = any day).
FOCUS_MUSCLES: dict[str, tuple[tuple[str, ...], str, frozenset | None]] = {
    "Chest": (("chest_iso", "incline_press"), "accessory", frozenset({"h_press", "incline_press", "chest_iso"})),
    "Back": (("h_pull", "v_pull"), "accessory", frozenset({"h_pull", "v_pull"})),
    "Shoulders": (("lat_delt", "rear_delt"), "isolation", frozenset({"v_press", "lat_delt", "rear_delt", "h_press"})),
    "Biceps": (("biceps",), "isolation", frozenset({"biceps", "h_pull", "v_pull"})),
    "Triceps": (("triceps",), "isolation", frozenset({"triceps", "h_press", "v_press", "incline_press"})),
    "Legs": (("single_leg", "knee_ext", "knee_flex"), "accessory",
             frozenset({"squat", "hinge", "single_leg", "knee_flex", "knee_ext", "calf"})),
    "Glutes": (("glute",), "accessory", frozenset({"squat", "hinge", "single_leg", "glute"})),
    "Abs": (("core",), "core", None),
}
MAX_FOCUS = 3
FOCUS_DAYS_CAP = 3          # a focus muscle gets an extra slot on at most this many days

# Some lifts need a bench, a bar or dip bars on top of their listed equipment. Machines
# bring their own seat or handles.
_NEEDS = (
    ("bench", re.compile(
        r"\bbench\b|incline|decline|seal row|hip thrust|chest[- ]supported|preacher|skull|"
        r"dumbbell fl(?:y|yes?)\b|pullover|lying tricep|bulgarian|step[- ]?up|seated dumbbell|"
        r"concentration curl", re.I)),
    ("pullup_bar", re.compile(r"pull[- ]?ups?\b|chin[- ]?ups?\b|hanging|toes[- ]to[- ]bar|muscle[- ]?up", re.I)),
    ("dip_bars", re.compile(r"(?<!bench )\bdips?\b", re.I)),
)


_GOBLET = re.compile(r"goblet", re.I)


def clean_equipment(keys) -> frozenset | None:
    """The athlete's equipment as a set of keys, or None for "no limit" (nothing sent,
    or everything ticked)."""
    if keys is None:
        return None
    got = frozenset(k for k in keys if k in EQUIPMENT_KEYS)
    return None if got == EQUIPMENT_KEYS else got


def clean_focus(muscles) -> list[str]:
    seen = []
    for m in muscles or []:
        if m in FOCUS_MUSCLES and m not in seen:
            seen.append(m)
    return seen[:MAX_FOCUS]


def fits_equipment(row: dict, available: frozenset | None) -> bool:
    """Whether the athlete can do this exercise with what they have. Bodyweight is
    always there; "Other" equipment (sleds, landmines, plates…) only with no limit."""
    if available is None:
        return True
    equipment = row.get("equipment") or "Other"
    name = row.get("name") or ""
    if equipment != "Bodyweight" and not any(equipment in _VALUES[k] for k in available):
        # A goblet squat is listed under kettlebells but is usually done with a dumbbell.
        if not (equipment == "Kettlebell" and "dumbbell" in available and _GOBLET.search(name)):
            return False
    if equipment == "Machine":
        return True
    return all(key in available or not rx.search(name) for key, rx in _NEEDS)


def equipment_values(available: frozenset | None) -> list[str] | None:
    """Library equipment values for the catalog filter."""
    if available is None:
        return None
    return sorted({v for k in available for v in _VALUES[k]} | {"Bodyweight"})


def equipment_labels(available: frozenset | None) -> list[str]:
    if available is None:
        return ["Full gym"]
    return [_LABEL[e["key"]] for e in EQUIPMENT if e["key"] in available] or ["Bodyweight only"]


# ── Time ─────────────────────────────────────────────────────────────

WARM_UP_MIN = 5
_WORK_SEC = 40              # one set's work, on average
_CHANGEOVER_MIN = 1.5       # set up the next exercise


def slot_minutes(sets: int, rest_sec: int | None) -> float:
    """Work for every set, rest between sets (not after the last), then the changeover."""
    return (sets * _WORK_SEC + max(0, sets - 1) * (rest_sec or 60)) / 60 + _CHANGEOVER_MIN


def day_minutes(exercises: list[dict]) -> int:
    """An honest estimate of a session's length, to the nearest 5 minutes."""
    total = WARM_UP_MIN + sum(slot_minutes(e.get("sets") or 3, e.get("rest_sec")) for e in exercises)
    return int(5 * math.floor(total / 5 + 0.5))


def setup_summary(setup: dict) -> str:
    """One line for the prompt and the plan header."""
    bits = []
    if setup.get("session_minutes"):
        bits.append(f"about {setup['session_minutes']} min a session")
    bits.append("equipment: " + ", ".join(equipment_labels(setup.get("equipment"))))
    if setup.get("focus_muscles"):
        bits.append("focus: " + ", ".join(setup["focus_muscles"]))
    return "; ".join(bits)
