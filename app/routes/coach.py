"""
AI fitness coach — turns a user's training history into a tailored workout
routine using the Google Gemini API.

The coach is a focused agent: it builds a compact "training profile" from the
athlete's logged sets (top movements, muscle-group coverage, frequency,
estimated 1RMs), hands that plus the chosen goal + days/week to the model, and
gets back a structured multi-day routine. Generation is review-then-save:

  POST /coach/generate   build profile → ask Gemini → return a draft plan (no write)
  POST /coach/save       persist a reviewed plan to coach_plans + create one
                         user-owned routine per day so it shows up in /routines
  DELETE /coach/plans/{id}

The athlete's training summary and request are sent to the Google Gemini API
(see the README privacy note); nothing is sent unless GEMINI_API_KEY is set.
"""

import asyncio
import json
import logging
import os
import time as _time
import uuid

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.db import WriteConflict, get_db, write_tx
from app.routes.auth import get_current_user
from app.utils import gemini
from app.utils.coach_plan import (  # noqa: F401  TEMPORARY re-export shims, removed in PR2
    _EXAMPLE_NOTE_PHRASES,
    catalog_names as _catalog_names,
    exercise_catalog as _exercise_catalog,
    max_weekly_repeats as _max_weekly_repeats,
    name_to_id_map as _name_to_id_map,
    normalise_plan as _normalise_plan,
    plan_quality_issues as _plan_quality_issues,
    repair_plan as _repair_plan,
    warm_caches,
)
from app.utils.training_profile import build_profile

router = APIRouter()

# Generation runs as a background job rather than a single long request: a
# routine can take tens of seconds (longer with rate-limit retries), which risks
# the response timeout of the Tailscale Funnel proxy in front of the app. The
# client kicks off a job and polls a fast status endpoint instead, so no single
# request is long-lived.
_JOBS: dict[str, dict] = {}
_JOBS_MAX = 50              # cap retained jobs (small self-hosted app; in-memory is fine)
_TASKS: set = set()        # keep task refs so they aren't GC'd mid-flight
_ACTIVE_BY_USER: dict[int, str] = {}  # uid -> in-flight job id (single-flight)
_GEN_LOCK = asyncio.Lock()  # one generation at a time; keeps us well inside the Gemini API rate limits

# Explicit queue so a burst of friends hitting "Generate" at once is bounded and
# ordered instead of piling up unbounded waiters. _GEN_LOCK already guarantees a
# single concurrent API call (so a burst can't trip Gemini's per-minute rate
# limits); the queue adds a hard depth cap + FIFO position reporting on top.
_QUEUE: list[str] = []     # job_ids waiting their turn, FIFO (for position display)
# Max users queued+running at once. Beyond this, new requests are rejected with a
# friendly 429 rather than waiting 30+ min behind a long line.
_MAX_QUEUE = int(os.environ.get("COACH_MAX_QUEUE", "5"))

# A job occupies a slot while it's waiting (queued) or running (processing).
_ACTIVE_STATES = ("queued", "processing")

_JOB_EVENTS: dict[str, asyncio.Queue] = {}  # per-job SSE queues for live progress


def _active_count() -> int:
    return sum(1 for j in _JOBS.values() if j.get("status") in _ACTIVE_STATES)


def _prune_jobs() -> None:
    # Never prune a job that is still queued or running — only trim finished
    # (done/error) history once it grows past the cap.
    if len(_JOBS) > _JOBS_MAX:
        finished = [k for k, v in _JOBS.items() if v.get("status") not in _ACTIVE_STATES]
        for key in finished[: len(_JOBS) - _JOBS_MAX]:
            _JOBS.pop(key, None)

GOALS = {
    "strength": "maximal strength — heavy compound lifts, 3–6 reps, long rest",
    "hypertrophy": "muscle growth — 8–15 reps, moderate load, controlled tempo",
    "balance": "correct imbalances — prioritise the athlete's under-trained muscle groups",
    "general": "well-rounded general fitness — mix of compound and accessory work",
}

# Per-goal set/rep/intensity prescriptions shown verbatim in the prompt.
_GOAL_PRESCRIPTION = {
    "strength": (
        "Primary compounds: 4–5 sets × 3–5 reps @ 80–90% 1RM, rest 3–5 min.\n"
        "Accessory lifts: 3 sets × 6–8 reps, rest 2 min.\n"
        "Prioritise: Squat, Deadlift, Bench Press, Overhead Press, Barbell Row."
    ),
    "hypertrophy": (
        "Primary compounds: 4 sets × 8–10 reps @ 65–75% 1RM, rest 90 sec.\n"
        "Accessory/isolation: 3–4 sets × 10–15 reps, rest 60 sec.\n"
        "Include at least one compound per muscle group."
    ),
    "balance": (
        "Focus every day on the athlete's most undertrained muscles.\n"
        "3–4 sets × 10–15 reps. Include unilateral movements (split squats,\n"
        "single-arm rows) to correct side-to-side asymmetry."
    ),
    "general": (
        "Alternate heavy (4 × 5–8) and moderate (3 × 10–12) sessions.\n"
        "Include at least one compound, one hinge, one vertical pull per week.\n"
        "Add 1–2 core exercises per session."
    ),
}

# Recommended split structure by days/week — shown in the prompt to guide day labels.
_SPLIT_GUIDE = {
    1: "Full Body — train every major muscle group in one session.",
    2: "Upper / Lower — Day 1: Upper Body, Day 2: Lower Body.",
    3: "Push / Pull / Legs  OR  Full Body × 3.",
    4: "Upper/Lower × 2 — Upper A · Lower A · Upper B · Lower B.",
    5: "Push / Pull / Legs / Upper / Lower.",
    6: "Push A · Pull A · Legs A · Push B · Pull B · Legs B.",
    7: "PPL × 2 + 1 active recovery or conditioning day.",
}


# Gemini structured-output schema, built per request. Pinning the day count
# (minItems == maxItems == days) and a minimum exercises-per-day pushes the
# small model toward a complete plan rather than stopping after one or two
# movements, while keeping output deterministic to parse.
def _plan_schema(days: int, min_ex: int = 6, max_ex: int = 8) -> dict:
    # `name` is a plain string, NOT an enum of the exercise catalog: Gemini
    # rejects schemas whose enum has more than a few dozen values (HTTP 400
    # "invalid argument" — verified live, 10 names OK / 40+ fail). Names are
    # constrained by the prompt's ALLOWED list and validated against the
    # library afterwards (_normalise_plan drops unknowns; the generation retries
    # when too many are dropped).
    name_field: dict = {"type": "string"}
    return {
        "type": "object",
        # additionalProperties:false on every object node stops the model from
        # spending output tokens on fields we never asked for and would just
        # discard in _normalise_plan() anyway — free savings on output tokens.
        "additionalProperties": False,
        "properties": {
            # maxLength caps below all follow the same reasoning as the existing
            # `note` cap: real values are short (a title like "3-Day Push/Pull/
            # Legs", a focus like "Upper Body", reps like "8-12"), and capping in
            # the schema — not just post-hoc in Python — means grammar-constrained
            # decoding can stop emitting those tokens early instead of generating
            # then discarding the overrun, which is where the time actually goes.
            "title": {"type": "string", "maxLength": 60},
            "summary": {"type": "string", "maxLength": 200},
            "days": {
                "type": "array",
                "minItems": days,
                "maxItems": days,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "focus": {"type": "string", "maxLength": 20},
                        "exercises": {
                            "type": "array",
                            "minItems": min_ex,
                            "maxItems": max_ex,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "name": name_field,
                                    "sets": {"type": "integer", "minimum": 1, "maximum": 20},
                                    "reps": {"type": "string", "maxLength": 12},
                                    # Hard cap via grammar-constrained decoding: long
                                    # notes dominate generation time (output tokens are
                                    # the wall-clock bottleneck).
                                    "note": {"type": "string", "maxLength": 90},
                                },
                                "required": ["name", "sets", "reps"],
                            },
                        },
                    },
                    "required": ["focus", "exercises"],
                },
            },
        },
        "required": ["title", "summary", "days"],
    }


# ── Pydantic request models ──────────────────────────────────────────

class GenerateIn(BaseModel):
    goal: str = Field(pattern=r"^(strength|hypertrophy|balance|general)$")
    days_per_week: int = Field(ge=1, le=7)
    focus_note: str = Field(default="", max_length=300)


class PlanExercise(BaseModel):
    name: str
    sets: int = Field(ge=1, le=20)
    reps: str = Field(max_length=20)
    note: str = ""


class PlanDay(BaseModel):
    focus: str = Field(max_length=80)
    exercises: list[PlanExercise]


class PlanIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=1000)
    goal: str = Field(pattern=r"^(strength|hypertrophy|balance|general)$")
    days_per_week: int = Field(ge=1, le=7)
    days: list[PlanDay]


class FeedbackIn(BaseModel):
    feedback: str = Field(pattern=r"^(too_easy|just_right|too_hard|skipped_often)$")


def _build_prompt(goal: str, days: int, profile: dict, catalog: dict, focus_note: str) -> str:
    """Render the human-readable context block handed to the model."""
    lines: list[str] = []

    # ── Goal & request ────────────────────────────────────────────────
    lines.append(f"GOAL: {GOALS[goal]}")
    lines.append(f"TRAINING DAYS PER WEEK: {days}")
    if focus_note.strip():
        lines.append(f"ATHLETE REQUEST: {focus_note.strip()}")

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
            f"- Weekly sets per muscle (target ≥10 for primary movers; ⬇ = under-trained): "
            + ", ".join(vol_parts) + "."
        )
    if profile.get("undertrained"):
        lines.append(
            f"- PRIORITY — under-trained muscles that MUST receive direct work every week: "
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
            f"- Strength stalled (no e1RM gain in 4 weeks) — vary rep range or swap variation: "
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

    # ── Split & prescription ──────────────────────────────────────────
    lines.append(f"RECOMMENDED SPLIT for {days} day(s)/week:")
    lines.append(_SPLIT_GUIDE.get(days, "Distribute muscle groups evenly across the week."))
    lines.append("")

    lines.append(f"PRESCRIPTION ({goal}):")
    lines.append(_GOAL_PRESCRIPTION[goal])
    lines.append("")

    # ── Exercise catalog ──────────────────────────────────────────────
    lines.append(
        "ALLOWED EXERCISES — use EXACT names from this list, grouped by Category/Muscle. "
        "Write the name only, without the [equipment] tag:"
    )
    for cat, muscle_map in catalog.items():
        for muscle, exercise_labels in muscle_map.items():
            lines.append(f"  {cat}/{muscle}: {', '.join(exercise_labels)}")
    lines.append("")

    # ── Rules ─────────────────────────────────────────────────────────
    lines.append(
        f"Now design a {days}-day training split. Return exactly {days} day(s). RULES:\n"
        "1. PERSONALISE. Use the athlete's actual movements and loads from the profile above — "
        "not a generic template. Reference their real e1RMs in the note field.\n"
        "2. COMPOUND FIRST. Each day opens with 1–2 heavy compound lifts (Squat / Deadlift / "
        "Bench Press / Overhead Press / Row / Pull-up), then accessories, then isolation last.\n"
        "3. COVER UNDER-TRAINED MUSCLES. Every priority muscle marked ⬇ must receive at least "
        "one direct exercise somewhere in the week.\n"
        "4. SPLIT LABEL. Give each day a clear focus matching the recommended split "
        "(e.g. 'Push', 'Pull', 'Legs', 'Upper Body', 'Full Body').\n"
        "5. VOLUME. Match the session length from the profile; use the set/rep scheme from the prescription.\n"
        "6. PROGRESSION. Every exercise note is ONE short overload cue, maximum 12 words "
        "(e.g. '@ 100 kg — add 2.5 kg when all reps clean'). Never write longer notes.\n"
        "7. RECOVERY. No heavy loading of the same primary muscle on consecutive days.\n"
        "8. RESPECT FATIGUE. Do not heavily load muscles marked 'trained ≤1 day ago' on Day 1.\n"
        "9. STALLED LIFTS. For plateaued exercises, change the rep range or substitute a "
        "variation from the ALLOWED list.\n"
        "10. EXACT NAMES. Use only exercise names from the ALLOWED list, spelled exactly.\n"
        f"11. VARIETY. No two days may be near-copies of each other, and no exercise may "
        f"appear on more than {_max_weekly_repeats(days)} day(s) in the week. Rotate "
        "variations instead (e.g. Bench Press one day, Incline Dumbbell Press another).\n"
        "12. RESPECT FLAGGED PAIN. If the athlete has flagged pain/discomfort above, do not "
        "program any movement that loads that area — substitute a comparable movement from "
        "the ALLOWED list that avoids it.\n"
        "13. AUTOREGULATE. A lift marked HIGH EFFORT keeps its load or backs off slightly; "
        "a lift marked LOW EFFORT gets a load increase. If recent wellness shows low energy, "
        "low motivation, or short sleep, favour moderate volume this week over an aggressive jump."
    )
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You are an elite strength & conditioning coach with 20 years of experience "
    "programming for powerlifters, bodybuilders, and general-population clients.\n\n"
    "Your ONLY job is to read the athlete's real logged data — their actual "
    "movements, estimated 1RMs, weekly muscle volumes, recovery state, and goal — "
    "and produce a specific, personalised weekly plan. A plan that could apply to "
    "anyone is a failed plan.\n\n"
    "Non-negotiable principles:\n"
    "- SPECIFICITY: reference the athlete's actual lifts and loads. If they squat "
    "120 kg e1RM, write '4×4 @ 100 kg' not '4×5'. If their chest is undertrained, "
    "every session in a full-body split includes a chest movement.\n"
    "- COMPOUND ANCHOR: every session opens with 1–2 heavy compound lifts from "
    "the allowed list, in order of loading demand.\n"
    "- PROGRESSIVE OVERLOAD: every exercise note specifies exactly how to progress "
    "(weight increment, rep target, or deload trigger).\n"
    "- BREVITY: each note is ONE cue of at most 12 words — never a paragraph.\n"
    "- RECOVERY: 48 h minimum between heavy loading of the same primary muscle.\n"
    "- VOLUME BALANCE: target 10–20 hard sets per primary muscle per week; "
    "undertrained muscles receive proportionally more work.\n"
    "- PROVEN SPLITS: Full Body, Upper/Lower, Push/Pull/Legs only.\n"
    "- ATHLETE'S OWN WORDS: the athlete's flagged pain, RPE trend, and journal wellness "
    "notes are real signal, not noise. Never program through flagged pain. Autoregulate "
    "load from RPE and back off volume when recent wellness is trending low.\n"
    "- VARIETY: every day in the week must be distinct — never return two days "
    "with the same exercise list, and rotate movement variations across the week "
    "rather than repeating one exercise on most days.\n\n"
    "You ONLY use exercise names from the provided ALLOWED list, spelled exactly. "
    "You return your answer strictly as JSON matching the schema — zero prose outside the JSON.\n\n"
    "Example of one well-formed day (follow this JSON SHAPE only. 'Exercise A' "
    "through 'Exercise E' are NOT real exercises — they do not exist in the "
    "ALLOWED list and must never appear in your answer. The weights, reps, and "
    "note wording are placeholders too. Copying this example's names, numbers, "
    "or phrasing — even for a different exercise — is a failed answer; every "
    "exercise, load, and rep target must be chosen fresh from the ALLOWED list "
    "and the athlete's own profile data above):\n"
    '{"focus": "Push", "exercises": ['
    '{"name": "Exercise A", "sets": 4, "reps": "5", "note": "@ 100 kg — add 2.5 kg when all reps clean"}, '
    '{"name": "Exercise B", "sets": 3, "reps": "8", "note": "@ 50 kg — add 1 rep/week to 10, then +2.5 kg"}, '
    '{"name": "Exercise C", "sets": 3, "reps": "10-12", "note": "@ 20 kg — increase by 2 kg when hitting 12"}, '
    '{"name": "Exercise D", "sets": 3, "reps": "15", "note": "@ 8 kg — slow eccentric, increase when form is solid"}, '
    '{"name": "Exercise E", "sets": 3, "reps": "12", "note": "@ 25 kg — add 2.5 kg every 2 weeks"}'
    "]}"
)


# ── Validation / name resolution ─────────────────────────────────────


async def _emit(job_id: str, event: dict) -> None:
    """Push a progress event into the SSE queue for this job (no-op if no subscriber)."""
    q = _JOB_EVENTS.get(job_id)
    if q is not None:
        await q.put(event)


# ── Routes ───────────────────────────────────────────────────────────

@router.get("/coach")
async def coach_page(request: Request):
    return RedirectResponse("/plan", status_code=301)


async def _run_generation(
    job_id: str, conn: aiosqlite.Connection, uid: int,
    goal: str, days: int, focus_note: str,
) -> None:
    """Background worker: build the profile, ask Gemini, validate, store result.

    Serialized by _GEN_LOCK so a burst of jobs can't trip Gemini's rate limits.
    Emits phase/token/done events to any connected SSE subscriber via _JOB_EVENTS."""

    async def _on_tokens(count: int) -> None:
        await _emit(job_id, {"type": "tokens", "count": count})

    try:
        async with _GEN_LOCK:
            # Our turn: leave the waiting queue and flip to processing.
            if job_id in _QUEUE:
                _QUEUE.remove(job_id)
            if _JOBS.get(job_id, {}).get("status") == "queued":
                _JOBS[job_id] = {"status": "processing", "user_id": uid}

            await _emit(job_id, {"type": "phase", "message": "Building your training profile…"})
            profile = await build_profile(conn, uid)
            catalog = await _exercise_catalog(conn, uid, profile.get("preferred_equipment"))
            prompt = _build_prompt(goal, days, profile, catalog, focus_note)
            asm = profile.get("avg_session_minutes")
            ex_target = max(4, min(10, round(asm / 7))) if asm else 7
            min_ex, max_ex = max(3, ex_target - 1), min(10, ex_target + 1)
            schema = _plan_schema(days, min_ex, max_ex)

            await _emit(job_id, {"type": "phase", "message": "Generating your plan…"})
            try:
                raw, model_used = await gemini.generate_json(
                    _SYSTEM_PROMPT, prompt, schema,
                    temperature=0.2,
                    on_tokens=_on_tokens,
                )
            except gemini.GeminiError:
                # One retry for a content-level failure (empty/malformed JSON).
                # gemini.chat_json() already retries transient failures (429/5xx/
                # timeouts) itself, so reaching this except means the API either
                # kept failing or answered with unusable content. Worth one more
                # attempt before failing the whole job; a permanent failure (bad
                # key, unknown model) just fails fast again.
                logging.warning("coach: chat_json failed, retrying once (job %s)", job_id)
                raw, model_used = await gemini.generate_json(
                    _SYSTEM_PROMPT, prompt, schema,
                    temperature=0.2,
                    on_tokens=_on_tokens,
                )
            name_map, _norm_map = await _name_to_id_map(conn)
            plan, dropped = _normalise_plan(raw, goal, days, name_map, _norm_map)
            issues = _plan_quality_issues(plan)

            # Auto-retry if the plan is thin (missing days / >30% dropped) OR
            # repetitive (copy-paste days, same exercise across too many days).
            total_exercises = sum(len(d["exercises"]) for d in plan["days"])
            total_dropped = len(dropped)
            drop_ratio = total_dropped / max(1, total_exercises + total_dropped)
            if len(plan["days"]) < days or drop_ratio > 0.30 or issues:
                logging.warning(
                    "coach retry: days=%d/%d dropped=%d/%d (%.0f%%) issues=%d",
                    len(plan["days"]), days, total_dropped,
                    total_exercises + total_dropped, drop_ratio * 100, len(issues),
                )
                await _emit(job_id, {"type": "phase", "message": "Refining your plan…"})
                retry_prompt = (
                    prompt
                    + "\n\nIMPORTANT: Use ONLY the exact exercise names from the ALLOWED list above. "
                    "Do not invent names. Return all "
                    + str(days)
                    + " day(s) — do not omit any."
                )
                if issues:
                    retry_prompt += (
                        "\n\nYour previous attempt had these problems — fix ALL of them:\n- "
                        + "\n- ".join(issues)
                        + f"\nEvery day must be distinct, and no exercise may appear on more "
                        f"than {_max_weekly_repeats(days)} day(s). Use different variations "
                        "(e.g. Bench Press one day, Incline Dumbbell Press another)."
                    )
                raw2, model_used2 = await gemini.generate_json(
                    _SYSTEM_PROMPT, retry_prompt, schema,
                    temperature=0.1,
                    on_tokens=_on_tokens,
                )
                plan2, dropped2 = _normalise_plan(raw2, goal, days, name_map, _norm_map)
                issues2 = _plan_quality_issues(plan2)
                # Keep whichever attempt is better: complete days first, then
                # fewer diversity issues, then fewer dropped names.
                if (len(plan2["days"]), -len(issues2), -len(dropped2)) > (
                    len(plan["days"]), -len(issues), -len(dropped)
                ):
                    plan, dropped = plan2, dropped2
                    model_used = model_used2

            # Deterministic guarantee: whatever the model returned, dedupe each
            # day and swap over-repeated exercises for same-muscle alternatives.
            plan, swaps = _repair_plan(plan, name_map)
            if swaps:
                logging.info("coach: repaired repetitive plan — %s", "; ".join(swaps))

        if not plan["days"]:
            err = "The model didn't return any usable exercises. Try again."
            _JOBS[job_id] = {"status": "error", "user_id": uid, "error": err}
            await _emit(job_id, {"type": "failed", "error": err})
            return

        final_dropped = sorted(set(dropped))

        # Auto-save as a draft so the plan survives browser close before the user confirms.
        # Remove any previous unconfirmed draft for this user first (one draft at a time).
        draft_id: int | None = None
        try:
            async with write_tx(conn):
                await conn.execute(
                    "DELETE FROM coach_plans WHERE user_id=? AND status='draft'",
                    (uid,),
                )
                async with conn.execute(
                    """INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, model, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'draft')""",
                    (uid, plan.get("title", "My Plan"), goal, days,
                     json.dumps(plan), model_used),
                ) as _c:
                    draft_id = _c.lastrowid
        except Exception:
            logging.warning("coach: could not auto-save draft for uid=%d", uid, exc_info=True)

        _JOBS[job_id] = {
            "status": "done", "user_id": uid,
            "plan": plan, "dropped": final_dropped, "model": model_used,
            "draft_id": draft_id,
        }
        await _emit(job_id, {
            "type": "done", "plan": plan,
            "dropped": final_dropped, "model": model_used,
            "draft_id": draft_id,
        })

    except gemini.GeminiError as exc:
        err = str(exc)
        _JOBS[job_id] = {"status": "error", "user_id": uid, "error": err}
        await _emit(job_id, {"type": "failed", "error": err})
    except Exception:
        logging.exception("coach generation failed (job %s)", job_id)
        err = "Generation failed unexpectedly. Check the server logs."
        _JOBS[job_id] = {"status": "error", "user_id": uid, "error": err}
        await _emit(job_id, {"type": "failed", "error": err})
    finally:
        if job_id in _QUEUE:        # defensive: drop from queue on any exit path
            _QUEUE.remove(job_id)
        if _ACTIVE_BY_USER.get(uid) == job_id:
            _ACTIVE_BY_USER.pop(uid, None)


@router.post("/coach/generate", status_code=202)
async def generate(
    body: GenerateIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Kick off generation as a background job and return its id immediately.
    The client polls GET /coach/generate/{job_id} or opens EventSource at
    /coach/stream/{job_id}. Keeps every request short so the long
    inference never hits the reverse-proxy timeout.

    Returns HTTP 202 with {job_id}."""
    uid = current_user["id"]

    # Single-flight: if this user already has a generation queued or running, hand
    # back the same job id. Repeated clicks (or a stale tab) then attach to the one
    # job instead of spawning several that would burn API quota.
    existing = _ACTIVE_BY_USER.get(uid)
    if existing and _JOBS.get(existing, {}).get("status") in _ACTIVE_STATES:
        return JSONResponse({"job_id": existing}, status_code=202)

    # Queue depth cap: reject (don't pile up) when too many are already waiting.
    if _active_count() >= _MAX_QUEUE:
        raise HTTPException(
            status_code=429,
            detail=f"The coach is busy — {_MAX_QUEUE} requests are already in the queue. "
                   "Give it a few minutes and try again.",
        )

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {"status": "queued", "user_id": uid}
    _QUEUE.append(job_id)
    _ACTIVE_BY_USER[uid] = job_id
    _prune_jobs()
    task = asyncio.create_task(
        _run_generation(job_id, conn, uid, body.goal, body.days_per_week, body.focus_note)
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return JSONResponse({"job_id": job_id}, status_code=202)


@router.get("/coach/generate/{job_id}")
async def generation_status(
    job_id: str,
    current_user=Depends(get_current_user),
):
    job = _JOBS.get(job_id)
    if not job or job["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Unknown generation job")
    if job["status"] == "queued":
        # 1-based position among everyone still waiting; 1 = next up.
        position = (_QUEUE.index(job_id) + 1) if job_id in _QUEUE else 1
        return JSONResponse({"status": "queued", "position": position, "ahead": max(0, position - 1)})
    if job["status"] == "processing":
        return JSONResponse({"status": "processing"})
    if job["status"] == "error":
        return JSONResponse({"status": "error", "error": job["error"]})
    return JSONResponse({
        "status": "done",
        "plan": job["plan"],
        "dropped": job["dropped"],
        "model": job["model"],
        "draft_id": job.get("draft_id"),
    })


@router.get("/coach/stream/{job_id}")
async def stream_job_events(
    job_id: str,
    current_user=Depends(get_current_user),
):
    """Server-Sent Events stream for live generation progress.

    Events: queued (initial position), phase (step message), tokens (running count),
    done (plan ready), failed (error message). Heartbeat comment every 20 s to keep
    the connection alive through proxies."""
    job = _JOBS.get(job_id)
    if not job or job["user_id"] != current_user["id"]:
        raise HTTPException(status_code=404, detail="Unknown generation job")

    # Register queue BEFORE returning the response — no await between here and
    # return, so the background task cannot run in between (asyncio is cooperative).
    q: asyncio.Queue = asyncio.Queue()
    _JOB_EVENTS[job_id] = q

    async def event_stream():
        try:
            # Fast-path: job may have finished before the browser opened the stream.
            current = _JOBS.get(job_id, {})
            status = current.get("status")
            if status == "done":
                payload = {
                    "type": "done", "plan": current["plan"],
                    "dropped": current["dropped"], "model": current["model"],
                    "draft_id": current.get("draft_id"),
                }
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                return
            if status == "error":
                yield f"event: failed\ndata: {json.dumps({'type': 'failed', 'error': current['error']})}\n\n"
                return
            if status == "queued":
                pos = (_QUEUE.index(job_id) + 1) if job_id in _QUEUE else 1
                yield f"event: queued\ndata: {json.dumps({'position': pos, 'ahead': max(0, pos - 1)})}\n\n"

            deadline = _time.monotonic() + 40 * 60
            while _time.monotonic() < deadline:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20.0)
                    etype = event.get("type", "message")
                    yield f"event: {etype}\ndata: {json.dumps(event)}\n\n"
                    if etype in ("done", "failed"):
                        return
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            _JOB_EVENTS.pop(job_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/coach/save", status_code=201)
async def save_plan(
    body: PlanIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    name_map, _ = await _name_to_id_map(conn)

    # Re-resolve names server-side; never trust client-supplied ids.
    stored_days = []
    routine_ids = []
    for day in body.days:
        resolved = []
        for ex in day.exercises:
            match = name_map.get(ex.name.lower())
            if not match:
                continue
            resolved.append({
                "exercise_id": match["id"],
                "name": match["name"],
                "sets": ex.sets,
                "reps": ex.reps,
                "note": ex.note,
            })
        if resolved:
            stored_days.append({"focus": day.focus or "Training", "exercises": resolved})

    if not stored_days:
        raise HTTPException(status_code=422, detail="Plan has no valid exercises")

    plan_obj = {
        "title": body.title.strip(),
        "summary": body.summary.strip(),
        "goal": body.goal,
        "days_per_week": body.days_per_week,
        "days": stored_days,
    }

    async with write_tx(conn):
        async with conn.execute(
            """
            INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, model)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uid, plan_obj["title"], body.goal, body.days_per_week,
             json.dumps(plan_obj), gemini.model()),
        ) as cur:
            plan_id = cur.lastrowid

        # Create one user-owned routine per day so the plan is usable in the logger.
        for i, day in enumerate(stored_days, start=1):
            label = f"{plan_obj['title']} · Day {i}: {day['focus']}"[:100]
            async with conn.execute(
                "INSERT INTO routines(name, user_id) VALUES (?, ?)",
                (label, uid),
            ) as cur:
                rid = cur.lastrowid
            for idx, ex in enumerate(day["exercises"]):
                await conn.execute(
                    "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,?)",
                    (rid, ex["exercise_id"], idx),
                )
            routine_ids.append(rid)

        # Embed routine_ids into the stored plan_json so GET /plan can surface them
        # without a secondary JOIN — no schema change required.
        plan_obj["routine_ids"] = routine_ids
        await conn.execute(
            "UPDATE coach_plans SET plan_json=? WHERE id=?",
            (json.dumps(plan_obj), plan_id),
        )
    return JSONResponse({"id": plan_id, "routine_ids": routine_ids}, status_code=201)


@router.post("/coach/plans/{plan_id}/confirm", status_code=201)
async def confirm_plan(
    plan_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Promote a draft plan to saved: create routines and flip status to 'saved'."""
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id, title, goal, days_per_week, plan_json FROM coach_plans "
        "WHERE id=? AND user_id=? AND status='draft'",
        (plan_id, uid),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Draft plan not found")

    plan_obj = json.loads(row["plan_json"] or "{}")
    title = (plan_obj.get("title") or row["title"] or "My Plan").strip()
    stored_days = plan_obj.get("days", [])
    if not stored_days:
        raise HTTPException(status_code=422, detail="Draft has no exercises")

    # Build name→id fallback for exercises that lack an explicit exercise_id
    # (real generated plans always have it via _normalise_plan; this guards edge cases).
    name_map, _ = await _name_to_id_map(conn)

    routine_ids = []
    async with write_tx(conn):
        # Compare-and-set first: of two concurrent confirms (double tap, two tabs)
        # only one may flip the draft to saved and create routines.
        async with conn.execute(
            "UPDATE coach_plans SET status='saved' "
            "WHERE id=? AND user_id=? AND status='draft'",
            (plan_id, uid),
        ) as cur:
            if cur.rowcount == 0:
                raise WriteConflict("This plan was already saved or replaced. Reload to see it.")
        for i, day in enumerate(stored_days, start=1):
            label = f"{title} · Day {i}: {day.get('focus', day.get('name', 'Training'))}"[:100]
            async with conn.execute(
                "INSERT INTO routines(name, user_id) VALUES (?, ?)", (label, uid),
            ) as cur:
                rid = cur.lastrowid
            for idx, ex in enumerate(day.get("exercises", [])):
                eid = ex.get("exercise_id")
                if not eid:
                    match = name_map.get((ex.get("name") or "").lower())
                    eid = match["id"] if match else None
                if not eid:
                    continue
                await conn.execute(
                    "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,?)",
                    (rid, eid, idx),
                )
            routine_ids.append(rid)

        plan_obj["routine_ids"] = routine_ids
        await conn.execute(
            "UPDATE coach_plans SET title=?, plan_json=? WHERE id=?",
            (title, json.dumps(plan_obj), plan_id),
        )
    return JSONResponse({"id": plan_id, "routine_ids": routine_ids}, status_code=201)


@router.delete("/coach/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    async with conn.execute(
        "SELECT id, plan_json FROM coach_plans WHERE id = ? AND user_id = ?",
        (plan_id, uid),
    ) as cur:
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plan not found")
    routine_ids = (json.loads(row["plan_json"] or "{}") or {}).get("routine_ids") or []
    async with write_tx(conn):
        if routine_ids:
            placeholders = ",".join("?" * len(routine_ids))
            await conn.execute(
                f"DELETE FROM routines WHERE id IN ({placeholders}) AND user_id = ?",
                (*routine_ids, uid),
            )
        await conn.execute("DELETE FROM coach_plans WHERE id = ? AND user_id = ?", (plan_id, uid))


@router.post("/coach/plans/{plan_id}/feedback", status_code=204)
async def set_plan_feedback(
    plan_id: int,
    body: FeedbackIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM coach_plans WHERE id = ? AND user_id = ?",
        (plan_id, current_user["id"]),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Plan not found")
    async with write_tx(conn):
        await conn.execute(
            "UPDATE coach_plans SET feedback = ? WHERE id = ? AND user_id = ?",
            (body.feedback, plan_id, current_user["id"]),
        )
