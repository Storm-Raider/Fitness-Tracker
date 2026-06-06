"""
AI fitness coach — turns a user's training history into a tailored workout
routine using a local Ollama LLM.

The coach is a focused agent: it builds a compact "training profile" from the
athlete's logged sets (top movements, muscle-group coverage, frequency,
estimated 1RMs), hands that plus the chosen goal + days/week to the model, and
gets back a structured multi-day routine. Generation is review-then-save:

  POST /coach/generate   build profile → ask Ollama → return a draft plan (no write)
  POST /coach/save       persist a reviewed plan to coach_plans + create one
                         user-owned routine per day so it shows up in /routines
  DELETE /coach/plans/{id}

All inference is on-device; nothing about the user leaves the host.
"""

import asyncio
import json
import logging
import os
import uuid

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import ollama
from app.utils.training_profile import build_profile

router = APIRouter()

# Generation runs as a background job rather than a single long request: on a
# Raspberry Pi a routine takes 3-4 minutes, which exceeds the response timeout
# of the Tailscale Funnel proxy in front of the app. The client kicks off a job
# and polls a fast status endpoint instead, so no single request is long-lived.
_JOBS: dict[str, dict] = {}
_JOBS_MAX = 50              # cap retained jobs (single-user Pi; in-memory is fine)
_TASKS: set = set()        # keep task refs so they aren't GC'd mid-flight
_ACTIVE_BY_USER: dict[int, str] = {}  # uid -> in-flight job id (single-flight)
_GEN_LOCK = asyncio.Lock()  # the Pi runs ONE generation at a time; serialize them

# Explicit queue so a burst of friends hitting "Generate" at once is bounded and
# ordered instead of piling up unbounded waiters. _GEN_LOCK already guarantees a
# single concurrent inference (so the Pi's CPU/RAM can't be doubled up); the
# queue adds a hard depth cap + FIFO position reporting on top.
_QUEUE: list[str] = []     # job_ids waiting their turn, FIFO (for position display)
# Max users queued+running at once. Beyond this, new requests are rejected with a
# friendly 429 rather than waiting 30+ min behind a long line.
_MAX_QUEUE = int(os.environ.get("COACH_MAX_QUEUE", "5"))

# A job occupies a slot while it's waiting (queued) or running (processing).
_ACTIVE_STATES = ("queued", "processing")


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

# Ollama structured-output schema, built per request. Pinning the day count
# (minItems == maxItems == days) and a minimum exercises-per-day pushes the
# small model toward a complete plan rather than stopping after one or two
# movements, while keeping output deterministic to parse.
def _plan_schema(days: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "days": {
                "type": "array",
                "minItems": days,
                "maxItems": days,
                "items": {
                    "type": "object",
                    "properties": {
                        "focus": {"type": "string"},
                        "exercises": {
                            "type": "array",
                            "minItems": 6,
                            "maxItems": 8,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "sets": {"type": "integer"},
                                    "reps": {"type": "string"},
                                    "note": {"type": "string"},
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


# Equipment ranked by how "staple" it is — biases the catalog toward
# compound barbell/dumbbell work over isolation machines within each category.
_EQUIP_RANK = {"Barbell": 0, "Dumbbell": 1, "Bodyweight": 2, "Cable": 3, "Machine": 4}

# Rank lookup for the conventional staples — lower index = higher priority.
_PRIORITY_RANK = {name.lower(): i for i, name in enumerate(_PRIORITY_EXERCISES)}


async def _exercise_catalog(conn: aiosqlite.Connection, uid: int) -> dict[str, list[str]]:
    """
    Full library of pickable exercises grouped by category (Cardio excluded),
    ordered so the model sees conventional staples first, then movements the
    athlete already trains, then by equipment tier.

    No per-category cap — the model is shown every available exercise so it
    cannot justify using a name that isn't in the library.
    """
    async with conn.execute(
        """
        SELECT e.name,
               COALESCE(e.category, 'Other') AS category,
               COALESCE(e.equipment, '') AS equipment,
               (SELECT COUNT(*) FROM sets s WHERE s.exercise_id = e.id AND s.user_id = ?) AS used
        FROM exercises e
        WHERE COALESCE(e.category, '') != 'Cardio'
        """,
        (uid,),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    rows.sort(key=lambda r: (
        _PRIORITY_RANK.get(r["name"].lower(), 999),  # conventional staples first
        0 if r["used"] else 1,                        # then exercises the user trains
        _EQUIP_RANK.get(r["equipment"], 5),           # then staple equipment
        r["name"],
    ))

    catalog: dict[str, list[str]] = {}
    for r in rows:
        catalog.setdefault(r["category"], []).append(r["name"])
    return catalog


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
    lines.append("")

    # ── Split & prescription ──────────────────────────────────────────
    lines.append(f"RECOMMENDED SPLIT for {days} day(s)/week:")
    lines.append(_SPLIT_GUIDE.get(days, "Distribute muscle groups evenly across the week."))
    lines.append("")

    lines.append(f"PRESCRIPTION ({goal}):")
    lines.append(_GOAL_PRESCRIPTION[goal])
    lines.append("")

    # ── Exercise catalog ──────────────────────────────────────────────
    lines.append("ALLOWED EXERCISES — use these EXACT names, no others:")
    for cat, names in catalog.items():
        lines.append(f"  {cat}: {', '.join(names)}")
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
        "5. VOLUME. 6–8 exercises per day; use the set/rep scheme from the prescription.\n"
        "6. PROGRESSION. Every exercise note must contain a specific overload cue "
        "(e.g. 'add 2.5 kg when all reps completed with good form', 'add 1 rep per week').\n"
        "7. RECOVERY. No heavy loading of the same primary muscle on consecutive days.\n"
        "8. RESPECT FATIGUE. Do not heavily load muscles marked 'trained ≤1 day ago' on Day 1.\n"
        "9. STALLED LIFTS. For plateaued exercises, change the rep range or substitute a "
        "variation from the ALLOWED list.\n"
        "10. EXACT NAMES. Use only exercise names from the ALLOWED list, spelled exactly."
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
    "- RECOVERY: 48 h minimum between heavy loading of the same primary muscle.\n"
    "- VOLUME BALANCE: target 10–20 hard sets per primary muscle per week; "
    "undertrained muscles receive proportionally more work.\n"
    "- PROVEN SPLITS: Full Body, Upper/Lower, Push/Pull/Legs only.\n\n"
    "You ONLY use exercise names from the provided ALLOWED list, spelled exactly. "
    "You return your answer strictly as JSON matching the schema — zero prose outside the JSON."
)


# ── Validation / name resolution ─────────────────────────────────────

async def _name_to_id_map(conn: aiosqlite.Connection) -> dict[str, dict]:
    """lowercased exercise name -> {id, name} for resolving model output."""
    async with conn.execute("SELECT id, name FROM exercises") as cur:
        return {r["name"].lower(): {"id": r["id"], "name": r["name"]} for r in await cur.fetchall()}


def _normalise_plan(raw: dict, goal: str, days: int, name_map: dict) -> tuple[dict, list[str]]:
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
            name = str(ex.get("name", "")).strip()
            match = name_map.get(name.lower())
            if not match:
                if name:
                    dropped.append(name)
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


# ── Routes ───────────────────────────────────────────────────────────

@router.get("/coach")
async def coach_page(request: Request):
    return RedirectResponse("/plan", status_code=301)


async def _run_generation(
    job_id: str, conn: aiosqlite.Connection, uid: int,
    goal: str, days: int, focus_note: str,
) -> None:
    """Background worker: build the profile, ask Ollama, validate, store result.

    Serialized by _GEN_LOCK so concurrent jobs can't thrash the Pi's CPU (which
    makes every generation crawl past the timeout)."""
    try:
        async with _GEN_LOCK:
            # Our turn: leave the waiting queue and flip to processing.
            if job_id in _QUEUE:
                _QUEUE.remove(job_id)
            if _JOBS.get(job_id, {}).get("status") == "queued":
                _JOBS[job_id] = {"status": "processing", "user_id": uid}
            profile = await build_profile(conn, uid)
            catalog = await _exercise_catalog(conn, uid)
            prompt = _build_prompt(goal, days, profile, catalog, focus_note)
            raw = await ollama.chat_json(
                _SYSTEM_PROMPT, prompt, _plan_schema(days),
                timeout=480.0, temperature=0.2,
            )
            name_map = await _name_to_id_map(conn)
            plan, dropped = _normalise_plan(raw, goal, days, name_map)
        if not plan["days"]:
            _JOBS[job_id] = {"status": "error", "user_id": uid,
                             "error": "The model didn't return any usable exercises. Try again."}
            return
        _JOBS[job_id] = {
            "status": "done", "user_id": uid,
            "plan": plan, "dropped": sorted(set(dropped)), "model": ollama.ollama_model(),
        }
    except ollama.OllamaError as exc:
        _JOBS[job_id] = {"status": "error", "user_id": uid, "error": str(exc)}
    except Exception:
        logging.exception("coach generation failed (job %s)", job_id)
        _JOBS[job_id] = {"status": "error", "user_id": uid,
                         "error": "Generation failed unexpectedly. Check the server logs."}
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
    The client polls GET /coach/generate/{job_id}. Keeps every request short so
    the long (minutes-on-Pi) inference never hits the reverse-proxy timeout."""
    uid = current_user["id"]

    # Single-flight: if this user already has a generation queued or running, hand
    # back the same job id. Repeated clicks (or a stale tab) then attach to the one
    # job instead of spawning several that would thrash the Pi.
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
    })


@router.post("/coach/save", status_code=201)
async def save_plan(
    body: PlanIn,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    name_map = await _name_to_id_map(conn)

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

    async with conn.execute(
        """
        INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, model)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (uid, plan_obj["title"], body.goal, body.days_per_week,
         json.dumps(plan_obj), ollama.ollama_model()),
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
    await conn.commit()
    return JSONResponse({"id": plan_id, "routine_ids": routine_ids}, status_code=201)


@router.delete("/coach/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    async with conn.execute(
        "SELECT id FROM coach_plans WHERE id = ? AND user_id = ?",
        (plan_id, current_user["id"]),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Plan not found")
    await conn.execute("DELETE FROM coach_plans WHERE id = ? AND user_id = ?", (plan_id, current_user["id"]))
    await conn.commit()


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
    await conn.execute(
        "UPDATE coach_plans SET feedback = ? WHERE id = ? AND user_id = ?",
        (body.feedback, plan_id, current_user["id"]),
    )
    await conn.commit()
