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

from app.utils.training_profile import has_pain_words


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
    include: set[str] | None = None,
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
            all_rows = [dict(r) for r in await cur.fetchall()]
        # The enforced boundary into prompts: a name an athlete (or an imported CSV)
        # could have used to smuggle instructions never reaches the model.
        _EXERCISE_BASE_ROWS = [r for r in all_rows if is_safe_exercise_name(r["name"])]
        for r in all_rows:
            if not is_safe_exercise_name(r["name"]):
                logging.warning("coach: leaving exercise %r out of prompts (name not allowed)", r["name"])

    rows = list(_EXERCISE_BASE_ROWS)
    # `include`: lower-case names that must be offered whatever the filters and caps say,
    # e.g. an exercise the athlete just asked for in the chat. Without this the model, told
    # to use only listed names, quietly substitutes a different exercise.
    forced = {n.lower() for n in (include or ())}

    # Filter to preferred equipment while always preserving conventional staples.
    # New users with no equipment history get the full library.
    if preferred_equipment:
        preferred_set = set(preferred_equipment)
        priority_names = {n.lower() for n in _PRIORITY_EXERCISES}
        rows = [
            r for r in rows
            if r["equipment"] in preferred_set or r["name"].lower() in priority_names
            or r["name"].lower() in forced
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
        if r["name"].lower() in forced:
            capped.append(r)                      # never counts against the bucket's 8
        elif _seen.get(key, 0) < 8:
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


def plan_quality_issues(plan: dict) -> list[str]:
    """Detect diversity failures the schema can't express: near-identical days
    and exercises repeated across too many days. Returns human-readable issue
    strings — non-empty list triggers a retry with this feedback in the prompt."""
    issues: list[str] = []
    days = plan["days"]
    sigs = [{ex["exercise_id"] for ex in d["exercises"]} for d in days]

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


def athlete_context(profile: dict, goal: str) -> list[str]:
    """Render what the coach knows about the athlete as prompt lines: feedback on
    the last plan, flagged pain (placed early: a small model attends best near the
    top and this is a safety constraint), then the training profile. Shared by plan
    generation and the chat so both describe the athlete identically."""
    lines: list[str] = []
    _fb_map = {
        "too_easy":      "previous plan was too easy — step up intensity and total volume",
        "just_right":    "previous plan difficulty was appropriate — maintain similar intensity",
        "too_hard":      "previous plan was too hard — cut volume or intensity by ~15%",
        "skipped_often": "athlete skipped often — simplify movements and reduce session length",
    }
    fb = profile.get("last_plan_feedback")
    if fb and fb in _fb_map:
        lines.append(f"FEEDBACK ON LAST PLAN: {_fb_map[fb]}")

    # Pain/injury flags — placed early and treated as non-negotiable, not
    # buried in the general profile, since a small model attends best to
    # instructions near the top and this is a safety constraint.
    if profile.get("injury_flags"):
        flagged = "; ".join(
            f"\"{f['text']}\"" + (f" (during {f['exercise']})" if f.get("exercise") else " (journal)")
            for f in profile["injury_flags"]
        )
        lines.append(
            "ATHLETE FLAGGED PAIN/DISCOMFORT — NON-NEGOTIABLE, avoid or substitute any "
            f"movement that loads the affected area: {flagged}."
        )
    lines.append("")

    # ── Athlete profile ───────────────────────────────────────────────
    lines.append("ATHLETE PROFILE (last 90 days):")

    if not profile["total_workouts"]:
        lines.append("- No workout history — design a beginner full-body programme.")
    else:
        spw = profile["sessions_per_week"]
        last = profile.get("last_day", "")
        lines.append(
            f"- {profile['total_workouts']} sessions logged"
            + (f", ~{spw}/week" if spw else "")
            + (f". Last session: {last}." if last else "")
        )

    # Preferred equipment
    equip = profile.get("preferred_equipment") or []
    if equip:
        lines.append(f"- Preferred equipment: {', '.join(equip)} (bias the plan toward these).")

    # Top movements
    if profile["top_exercises"]:
        movers = ", ".join(
            f"{e['name']} ({e['sets']} sets)" for e in profile["top_exercises"][:8]
        )
        lines.append(f"- Most-trained movements: {movers}.")

    # Estimated 1RMs + load targets for the chosen goal
    if profile["top_lifts"]:
        pct_map = {"strength": 0.825, "hypertrophy": 0.70, "balance": 0.70, "general": 0.75}
        pct = pct_map[goal]
        lift_parts = []
        for l in profile["top_lifts"]:
            e1rm = l["e1rm"]
            target = round(e1rm * pct / 2.5) * 2.5  # round to nearest 2.5 kg
            lift_parts.append(f"{l['name']} e1RM {e1rm}kg (use ~{target}kg)")
        lines.append(f"- Estimated 1RMs and suggested working loads: {'; '.join(lift_parts)}.")

    # Weekly volume per muscle with undertrained flag
    wvol = profile.get("avg_weekly_sets") or {}
    if wvol:
        ut = set(profile.get("undertrained") or [])
        vol_parts = []
        for m, v in sorted(wvol.items(), key=lambda x: -x[1]):
            flag = " ⬇" if m in ut else ""
            vol_parts.append(f"{m}: {v}{flag}")
        lines.append(
            "- Weekly sets per muscle (target ≥10 for primary movers; ⬇ = under-trained): "
            + ", ".join(vol_parts) + "."
        )
    if profile.get("undertrained"):
        lines.append(
            "- PRIORITY — under-trained muscles that MUST receive direct work every week: "
            + ", ".join(profile["undertrained"]) + "."
        )

    # Recovery state
    rec = profile.get("muscle_recovery") or {}
    fatigued = [m for m, s in rec.items() if s == "fatigued"]
    recovering = [m for m, s in rec.items() if s == "recovering"]
    if fatigued:
        lines.append(
            f"- Muscles trained ≤1 day ago — do NOT load heavily on Day 1: {', '.join(fatigued)}."
        )
    if recovering:
        lines.append(
            f"- Muscles trained 2–3 days ago — keep moderate until Day 3+: {', '.join(recovering)}."
        )

    # Stalled lifts
    if profile.get("stalled"):
        lines.append(
            "- Strength stalled (no e1RM gain in 4 weeks) — vary rep range or swap variation: "
            + ", ".join(profile["stalled"]) + "."
        )

    # Bodyweight — for BW exercise notation and relative load context
    bw = profile.get("bodyweight_kg")
    if bw:
        lines.append(f"- Current bodyweight: {bw} kg (use for BW exercise load notation, e.g. 'BW+20 kg').")

    # Average session length — guides exercise count per day
    asm = profile.get("avg_session_minutes")
    if asm:
        # ~7 min/exercise is a practical estimate for warm-up + working sets + rest
        ex_count = max(4, min(10, round(asm / 7)))
        lines.append(
            f"- Average session length: {asm} min → target ~{ex_count} exercises per session."
        )

    # RPE trend (last 14 days) — autoregulation signal from the athlete's own
    # effort ratings, not just weight/rep numbers.
    if profile.get("high_effort_lifts"):
        parts = [f"{l['name']} (avg RPE {l['avg_rpe']})" for l in profile["high_effort_lifts"]]
        lines.append(
            "- HIGH EFFORT — near failure the last 2 weeks, hold load or consider a deload: "
            + ", ".join(parts) + "."
        )
    if profile.get("low_effort_lifts"):
        parts = [f"{l['name']} (avg RPE {l['avg_rpe']})" for l in profile["low_effort_lifts"]]
        lines.append(
            "- LOW EFFORT — reps have felt easy the last 2 weeks, room to add load: "
            + ", ".join(parts) + "."
        )

    # Journal wellness — sleep/energy/motivation the athlete logged, and any
    # recent free-text comments. Previously invisible to the coach entirely.
    wellness = profile.get("wellness") or {}
    if wellness.get("avg_sleep_hrs") is not None or wellness.get("low_energy_days") or wellness.get("low_motivation_days"):
        bits = []
        if wellness.get("avg_sleep_hrs") is not None:
            bits.append(f"avg sleep {wellness['avg_sleep_hrs']}h/night")
        if wellness.get("low_energy_days"):
            bits.append(f"{wellness['low_energy_days']} low-energy day(s) logged")
        if wellness.get("low_motivation_days"):
            bits.append(f"{wellness['low_motivation_days']} low-motivation day(s) logged")
        lines.append(
            "- RECENT WELLNESS (journal, last 14 days): " + ", ".join(bits)
            + ". If energy/sleep is trending low, favour moderate volume over a big jump in load."
        )
    if wellness.get("recent_notes"):
        notes = "; ".join(f"\"{n['note']}\" ({n['date']})" for n in wellness["recent_notes"][:3])
        lines.append(f"- Athlete's recent journal comments: {notes}.")

    if profile.get("recent_set_notes"):
        notes = "; ".join(
            f"{n['name']}: \"{n['notes']}\" ({n['days_ago']}d ago)" for n in profile["recent_set_notes"][:5]
        )
        lines.append(f"- Athlete's recent workout comments: {notes}.")

    # User-set strength targets — plan should progress toward these
    goals_list = profile.get("exercise_goals") or []
    if goals_list:
        goal_parts = [f"{g['name']} → {g['target_kg']} kg" for g in goals_list]
        lines.append(
            "- ATHLETE STRENGTH GOALS (design the plan to progress toward these): "
            + "; ".join(goal_parts) + "."
        )
    lines.append("")
    return lines


# Flagged-pain area -> movements that load it. A small model sees the pain flag but
# does not reliably know that Leg Press or Leg Extension load the knee (live eval:
# it kept programming both), so the task line spells the area out. Matching is a
# plain keyword check on the athlete's own words; an unrecognised area still gets
# the generic constraint.
# Each entry: (words in the athlete's text, area, movements as prose for the prompt, a
# regex over exercise NAMES for the same movements, used to filter swap alternatives).
_PAIN_AVOID = [
    (("knee", "patella", "acl", "meniscus"), "knee",
     "squats, lunges, leg presses, leg extensions, step-ups and jumping",
     r"squat|lunge|leg press|leg extension|step[- ]?up|jump|sissy"),
    (("shoulder", "rotator"), "shoulder",
     "overhead pressing, dips, upright rows and behind-the-neck work",
     r"overhead|military|upright row|\bdip|behind|snatch|handstand|arnold|push press|jerk"),
    (("lower back", "low back", "lumbar", "spine", "disc", "sciatic"), "lower back",
     "deadlifts, good mornings, bent-over rows and heavy squats",
     r"deadlift|good morning|bent[- ]?over|squat|clean|snatch|back extension|hyperextension"),
    (("elbow",), "elbow", "skull crushers, dips, close-grip pressing and heavy curls",
     r"skull|\bdip|close[- ]grip|curl"),
    (("hip",), "hip", "deep squats, lunges, sumo deadlifts and hip-thrust variations that pinch",
     r"squat|lunge|sumo|hip thrust|bulgarian"),
]


def _pain_areas(profile: dict, said: str = ""):
    """The _PAIN_AVOID entries that match the profile's pain flags and what was said."""
    flags = [f.get("text", "") for f in (profile.get("injury_flags") or [])]
    if said and has_pain_words(said):
        flags.append(said)
    text = " ".join(flags).lower()
    return bool(flags), [e for e in _PAIN_AVOID if any(w in text for w in e[0])]


def pain_avoid_pattern(profile: dict, said: str = "") -> "re.Pattern | None":
    """A regex over exercise names for every movement the recognised painful areas rule
    out, or None when no recognised area is in play. Used to keep such exercises out of the
    alternatives offered for a swap."""
    _, areas = _pain_areas(profile, said)
    if not areas:
        return None
    return re.compile("|".join(f"(?:{a[3]})" for a in areas), re.I)


def pain_constraint(profile: dict, said: str = "") -> str:
    """One sentence for the end of the prompt restating pain as a hard constraint, with the
    movements each recognised area rules out. Pain comes from the profile's flags and, in
    the chat, from what the athlete just said (`said`, only when it contains a pain word).
    Empty when there is none."""
    any_pain, areas = _pain_areas(profile, said)
    if not any_pain:
        return ""
    avoid = [f"for the {area}: no {movements}" for _words, area, movements, _rx in areas]
    out = ("The athlete flagged pain: no exercise on any day may load the painful area, "
           "so choose alternatives from the ALLOWED list.")
    if avoid:
        out += " " + "; ".join(avoid) + "."
    return out


def staple_rank(row: dict) -> tuple[int, int]:
    """Sort key putting conventional staples first, then barbell-first equipment order."""
    return (_PRIORITY_RANK.get(row["name"].lower(), 999), _EQUIP_RANK.get(row["equipment"], 5))


async def exercise_rows(conn: aiosqlite.Connection) -> list[dict]:
    """The library as {name, category, equipment, primary_muscle} rows (no cardio, names
    that are safe to show), from the same cache the catalog uses."""
    await exercise_catalog(conn, 0, None)          # fills the cache; the result is not needed
    return [dict(r) for r in (_EXERCISE_BASE_ROWS or [])]


def catalog_lines(catalog: dict) -> list[str]:
    """The ALLOWED EXERCISES block of a prompt, shared by generation and chat."""
    lines = [
        "ALLOWED EXERCISES — use EXACT names from this list, grouped by Category/Muscle. "
        "Write the name only, without the [equipment] tag:"
    ]
    for cat, muscle_map in catalog.items():
        for muscle, exercise_labels in muscle_map.items():
            lines.append(f"  {cat}/{muscle}: {', '.join(exercise_labels)}")
    return lines


# ── Exercise names: what may reach a prompt ──────────────────────────
# Letters and digits (any script), space, and - ' ( ) / + & . , up to 60 characters.
# No quotes, brackets, colons, angle brackets, newlines or control characters: nothing
# that can close a quoted block, fake a section header or look like a tag. All 176 names
# in the built-in library pass (measured), so this does not touch the seeded catalog.
_SAFE_NAME = re.compile(r"(?:[^\W_]|[ \-'()/+&.,]){1,60}")
NAME_RULE = "Exercise names can use letters, numbers, spaces and - ' ( ) / + & . , (up to 60 characters)."


def is_safe_exercise_name(name) -> bool:
    return isinstance(name, str) and name == name.strip() and bool(_SAFE_NAME.fullmatch(name))


def validate_exercise_name(name) -> str:
    """The stripped name, or ValueError(NAME_RULE). Used when an exercise is created."""
    cleaned = name.strip() if isinstance(name, str) else ""
    if not is_safe_exercise_name(cleaned):
        raise ValueError(NAME_RULE)
    return cleaned


def invalidate_exercise_caches() -> None:
    """Forget the process-lifetime exercise caches. Call after anything that adds or
    removes exercises (creating one, a CSV import) so the next prompt and name match see it."""
    global _EXERCISE_BASE_ROWS, _NAME_MAP_CACHE
    _EXERCISE_BASE_ROWS = None
    _NAME_MAP_CACHE = None
