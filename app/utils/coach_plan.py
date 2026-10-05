"""
Plan logic for the AI coach, shared by the generation routes and (soon) the chat.

Moved verbatim from app/routes/coach.py with no behaviour change: the exercise
catalog the model chooses from, the name -> exercise id resolution, normalising
and repairing a model-written plan, and the quality heuristics. Keeping it in
app/utils lets pure chat logic import it without depending on a route module.

Public names have no leading underscore. app/routes/coach.py keeps temporary
aliases under the old private names so existing callers and tests are unchanged.
"""

import logging
import re

import aiosqlite


# Equipment ranked by how "staple" it is — biases the catalog toward
# compound barbell/dumbbell work over isolation machines within each category.
_EQUIP_RANK = {"Barbell": 0, "Dumbbell": 1, "Bodyweight": 2, "Cable": 3, "Machine": 4}


# Conventional movements that ALWAYS appear at the top of the catalog,
# regardless of whether the athlete has trained them.  Prevents the model
# from building plans out of obscure machines because the user happens to
# have logged only one movement.
_PRIORITY_EXERCISES = [
    # Legs
    "Back Squat", "Deadlift", "Romanian Deadlift", "Leg Press",
    "Bulgarian Split Squat", "Hip Thrust", "Leg Curl", "Leg Extension",
    "Lunge", "Calf Raise",
    # Push
    "Bench Press", "Overhead Press", "Incline Bench Press",
    "Dumbbell Bench Press", "Incline Dumbbell Press",
    "Dumbbell Shoulder Press", "Dip", "Lateral Raise", "Tricep Pushdown",
    # Pull
    "Barbell Row", "Chin-up", "Pull-up", "Lat Pulldown", "Cable Row",
    "Dumbbell Row", "Face Pull", "Barbell Curl", "Hammer Curl",
    # Core / Full Body
    "Plank", "Hanging Leg Raise", "Ab Wheel Rollout",
    "Farmer's Carry", "Kettlebell Swing",
]


# Rank lookup for the conventional staples — lower index = higher priority.
_PRIORITY_RANK = {name.lower(): i for i, name in enumerate(_PRIORITY_EXERCISES)}


# Process-lifetime caches for data that is seeded from a static Python dict and
# never mutated at runtime.  Both are cleared automatically on service restart.
_EXERCISE_BASE_ROWS: list[dict] | None = None   # raw exercise rows for catalog


_NAME_MAP_CACHE: tuple[dict, dict] | None = None  # (name_map, norm_map)


async def exercise_catalog(
    conn: aiosqlite.Connection, uid: int,
    preferred_equipment: list[str] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """
    Library grouped as {category: {primary_muscle: ['Name [Equipment]', ...]}}
    ordered so conventional staples appear first within each group.

    When preferred_equipment is provided the catalog is filtered to exercises
    that use that equipment OR are conventional staples — keeping the prompt
    short enough for a small model to handle without losing core movements.
    """
    global _EXERCISE_BASE_ROWS
    if _EXERCISE_BASE_ROWS is None:
        async with conn.execute(
            """
            SELECT e.name,
                   COALESCE(e.category, 'Other') AS category,
                   COALESCE(e.equipment, '') AS equipment,
                   (SELECT em.muscle FROM exercise_muscles em
                    WHERE em.exercise_id = e.id AND em.is_primary = 1
                    ORDER BY em.rowid ASC LIMIT 1) AS primary_muscle
            FROM exercises e
            WHERE COALESCE(e.category, '') != 'Cardio'
            """
        ) as cur:
            _EXERCISE_BASE_ROWS = [dict(r) for r in await cur.fetchall()]

    rows = list(_EXERCISE_BASE_ROWS)

    # Filter to preferred equipment while always preserving conventional staples.
    # New users with no equipment history get the full library.
    if preferred_equipment:
        preferred_set = set(preferred_equipment)
        priority_names = {n.lower() for n in _PRIORITY_EXERCISES}
        rows = [
            r for r in rows
            if r["equipment"] in preferred_set or r["name"].lower() in priority_names
        ]

    rows.sort(key=lambda r: (
        _PRIORITY_RANK.get(r["name"].lower(), 999),
        _EQUIP_RANK.get(r["equipment"], 5),
        r["name"],
    ))

    # Cap entries per muscle bucket — the sort already put the best choices first,
    # so we keep the top 8. Cuts prompt tokens by ~50% on unfiltered catalogs.
    _seen: dict[tuple, int] = {}
    capped = []
    for r in rows:
        key = (r["category"], r["primary_muscle"] or r["category"])
        if _seen.get(key, 0) < 8:
            _seen[key] = _seen.get(key, 0) + 1
            capped.append(r)
    rows = capped

    catalog: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        cat = r["category"]
        muscle = r["primary_muscle"] or cat
        equip = r["equipment"]
        label = f"{r['name']} [{equip}]" if equip else r["name"]
        catalog.setdefault(cat, {}).setdefault(muscle, []).append(label)
    return catalog


def catalog_names(catalog: dict[str, dict[str, list[str]]]) -> list[str]:
    """Extract unique plain exercise names from catalog (strips [Equipment] suffix)."""
    names, seen = [], set()
    for muscle_map in catalog.values():
        for labels in muscle_map.values():
            for label in labels:
                name = label.split(" [")[0] if " [" in label else label
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


async def warm_caches(conn: aiosqlite.Connection) -> None:
    """Pre-populate exercise caches during lifespan startup (runs before first request)."""
    await exercise_catalog(conn, 0)
    await name_to_id_map(conn)
    logging.info(
        "coach: caches warmed — %d exercise rows, %d name entries",
        len(_EXERCISE_BASE_ROWS or []),
        len((_NAME_MAP_CACHE or ({},))[0]),
    )


async def name_to_id_map(conn: aiosqlite.Connection) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Returns (name_map, norm_map).

    name_map: lowercased exact name → {id, name}
    norm_map: model-output variants → canonical {id, name}, covering:
      - hyphen ↔ space          ("pull up"        → Pull-up)
      - +s / +es plural forms   ("lateral raises" → Lateral Raise,
                                  "overhead presses" → Overhead Press)
    Normalized variants are only added when they don't collide with a real name,
    so "Press" (real) is never overwritten by stripping 's' from "Presses".
    """
    global _NAME_MAP_CACHE
    if _NAME_MAP_CACHE is not None:
        return _NAME_MAP_CACHE
    async with conn.execute("SELECT id, name FROM exercises") as cur:
        rows = await cur.fetchall()
    name_map = {r["name"].lower(): {"id": r["id"], "name": r["name"]} for r in rows}
    norm_map: dict[str, dict] = {}
    for key, val in name_map.items():
        # hyphen → space ("pull-up" → "pull up")
        spaced = key.replace("-", " ")
        if spaced != key and spaced not in name_map:
            norm_map[spaced] = val
        for base in (key, spaced):
            # +s plural  ("lateral raise" → "lateral raises")
            for suffix in ("s", "es"):
                variant = base + suffix
                if variant not in name_map and variant not in norm_map:
                    norm_map[variant] = val
    _NAME_MAP_CACHE = (name_map, norm_map)
    return _NAME_MAP_CACHE


# The prompt lists exercises as "Name [Equipment]". Models sometimes copy the whole
# label, so strip a trailing [tag] before resolving a name.
_EQUIPMENT_TAG = re.compile(r"\s*\[[^\]]*\]\s*$")


def normalise_plan(raw: dict, goal: str, days: int, name_map: dict, norm_map: dict | None = None) -> tuple[dict, list[str]]:
    """
    Coerce the model's output into our shape, resolve exercise names to real
    library entries, drop anything unrecognised, and cap to `days` days.

    Returns (plan, dropped_names).
    """
    dropped: list[str] = []
    out_days = []
    for day in (raw.get("days") or [])[:days]:
        exercises = []
        for ex in (day.get("exercises") or []):
            name = _EQUIPMENT_TAG.sub("", str(ex.get("name", "")).strip()).strip()
            match = name_map.get(name.lower())
            if not match and name:
                # Recover common model errors without risking false-positive fuzzy matches:
                # 1) hyphen/en-dash ↔ space  ("Pull Up" → "Pull-up")
                # 2) trailing plural 's'      ("Lateral Raises" → "Lateral Raise")
                key = name.lower()
                alt = norm_map and (norm_map.get(key) or norm_map.get(key.replace("-", " ").replace("–", " ")))
                match = alt
                if not match:
                    dropped.append(name)
                    continue
            elif not match:
                continue
            try:
                sets = max(1, min(20, int(ex.get("sets") or 3)))
            except (TypeError, ValueError):
                sets = 3
            exercises.append({
                "exercise_id": match["id"],
                "name": match["name"],
                "sets": sets,
                "reps": str(ex.get("reps", "")).strip()[:20] or "8-12",
                "note": str(ex.get("note", "")).strip()[:240],
            })
        if exercises:
            out_days.append({
                "focus": str(day.get("focus", "")).strip()[:80] or "Training",
                "exercises": exercises,
            })

    plan = {
        "title": str(raw.get("title", "")).strip()[:120] or "Coach Routine",
        "summary": str(raw.get("summary", "")).strip()[:1000],
        "goal": goal,
        "days_per_week": days,
        "days": out_days,
    }
    return plan, dropped


def max_weekly_repeats(days_per_week: int) -> int:
    """How many days one exercise may appear on before the plan counts as repetitive.

    Mirrors conventional programming: U/L and PPL repeat a movement at most
    twice a week; only high-frequency 6–7-day splits justify a third exposure."""
    if days_per_week <= 2:
        return 1
    if days_per_week <= 5:
        return 2
    return 3


# Exact cue-text fragments from _SYSTEM_PROMPT's worked example. Renaming the
# example's exercises to unresolvable placeholders (Exercise A-E) stops the
# model from copying them at the name level — an unresolvable name is dropped
# by normalise_plan() — but on a small model under weak
# personalization signal (e.g. no logged history for that muscle group) it can
# still fall back to reproducing the example's weights/rep-scheme/note wording
# verbatim for whatever real exercise it does pick. Live-verified: a real
# generation reused 4 of these 5 phrases byte-for-byte, sets/reps included,
# for an athlete with no push-day history. A SINGLE match is not itself
# suspicious — "add 2.5 kg when all reps clean" is generic, plausible advice a
# model could legitimately reach for on its own — but several matches in one
# plan is a strong copying signal, so this is threshold-gated, not a hard
# per-phrase ban.
_EXAMPLE_NOTE_PHRASES = [
    "add 2.5 kg when all reps clean",
    "add 1 rep/week to 10, then +2.5 kg",
    "increase by 2 kg when hitting 12",
    "slow eccentric, increase when form is solid",
    "add 2.5 kg every 2 weeks",
]


_EXAMPLE_COPY_THRESHOLD = 3  # phrase matches at/above this count flags the plan


def plan_quality_issues(plan: dict) -> list[str]:
    """Detect diversity failures the schema can't express: near-identical days
    and exercises repeated across too many days. Returns human-readable issue
    strings — non-empty list triggers a retry with this feedback in the prompt."""
    issues: list[str] = []
    days = plan["days"]
    sigs = [{ex["exercise_id"] for ex in d["exercises"]} for d in days]

    all_notes = " ".join(
        ex.get("note", "") for d in days for ex in d["exercises"]
    )
    phrase_hits = [p for p in _EXAMPLE_NOTE_PHRASES if p in all_notes]
    if len(phrase_hits) >= _EXAMPLE_COPY_THRESHOLD:
        issues.append(
            f"{len(phrase_hits)} exercise notes reuse the system prompt's worked "
            "example wording verbatim instead of the athlete's own data — every "
            "note must be computed fresh, not copied from the example"
        )

    for i in range(len(days)):
        for j in range(i + 1, len(days)):
            if not sigs[i] or not sigs[j]:
                continue
            overlap = len(sigs[i] & sigs[j]) / min(len(sigs[i]), len(sigs[j]))
            if overlap >= 0.6:
                issues.append(
                    f"Day {i + 1} ('{days[i]['focus']}') and Day {j + 1} "
                    f"('{days[j]['focus']}') share {round(overlap * 100)}% of their "
                    "exercises — the days must be distinct"
                )

    cap = max_weekly_repeats(plan["days_per_week"])
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    for d, sig in zip(days, sigs):
        for eid in sig:
            counts[eid] = counts.get(eid, 0) + 1
        for ex in d["exercises"]:
            names[ex["exercise_id"]] = ex["name"]
    for eid, c in counts.items():
        if c > cap:
            issues.append(f"{names[eid]} appears on {c} days (maximum {cap} per week)")
    return issues


def repair_plan(plan: dict, name_map: dict) -> tuple[dict, list[str]]:
    """Deterministic last line of defence against a repetitive model output.

    1. Removes duplicate exercises within each day (keeps the first).
    2. Caps how many days an exercise appears on (max_weekly_repeats); excess
       occurrences are swapped for an unused same-muscle alternative from the
       library, preferring conventional staples.

    Runs after every generation regardless of retries, so a saved plan can
    never contain copy-paste days even when the small model ignores the prompt.
    Returns (plan, swap_descriptions) — swaps are logged, not user-facing."""
    rows = _EXERCISE_BASE_ROWS or []
    muscle_of = {r["name"].lower(): (r["primary_muscle"] or r["category"]) for r in rows}
    by_muscle: dict[str, list[str]] = {}
    for r in sorted(rows, key=lambda r: (
        _PRIORITY_RANK.get(r["name"].lower(), 999),
        _EQUIP_RANK.get(r["equipment"], 5),
        r["name"],
    )):
        by_muscle.setdefault(r["primary_muscle"] or r["category"], []).append(r["name"])

    swaps: list[str] = []
    cap = max_weekly_repeats(plan["days_per_week"])

    # 1 — within-day dedupe.
    for day in plan["days"]:
        seen: set[int] = set()
        deduped = []
        for ex in day["exercises"]:
            if ex["exercise_id"] in seen:
                continue
            seen.add(ex["exercise_id"])
            deduped.append(ex)
        day["exercises"] = deduped

    # 2 — cross-day cap. Earlier days keep the exercise; later occurrences get
    # swapped for a same-muscle alternative that isn't already in that day and
    # isn't itself at the cap.
    used_count: dict[int, int] = {}
    for day in plan["days"]:
        day_ids = {ex["exercise_id"] for ex in day["exercises"]}
        for ex in day["exercises"]:
            eid = ex["exercise_id"]
            if used_count.get(eid, 0) < cap:
                used_count[eid] = used_count.get(eid, 0) + 1
                continue
            muscle = muscle_of.get(ex["name"].lower())
            replacement = None
            for cand in by_muscle.get(muscle, []):
                cand_match = name_map.get(cand.lower())
                if not cand_match:
                    continue
                cid = cand_match["id"]
                if cid == eid or cid in day_ids or used_count.get(cid, 0) >= cap:
                    continue
                replacement = cand_match
                break
            if replacement:
                swaps.append(f"{ex['name']} → {replacement['name']}")
                ex["exercise_id"] = replacement["id"]
                ex["name"] = replacement["name"]
                ex["note"] = "Variation swap for weekly variety — match the loading of the movement it replaces."
                day_ids.add(replacement["id"])
                used_count[replacement["id"]] = used_count.get(replacement["id"], 0) + 1
            else:
                # No viable alternative — accept the repeat rather than drop work.
                used_count[eid] = used_count.get(eid, 0) + 1
    return plan, swaps
