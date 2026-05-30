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
import uuid

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import ollama
from app.utils.render import templates

router = APIRouter()

# Generation runs as a background job rather than a single long request: on a
# Raspberry Pi a routine takes 3-4 minutes, which exceeds the response timeout
# of the Tailscale Funnel proxy in front of the app. The client kicks off a job
# and polls a fast status endpoint instead, so no single request is long-lived.
_JOBS: dict[str, dict] = {}
_JOBS_MAX = 50              # cap retained jobs (single-user Pi; in-memory is fine)
_TASKS: set = set()        # keep task refs so they aren't GC'd mid-flight
_ACTIVE_BY_USER: dict[int, str] = {}  # uid -> in-flight job id (single-flight)
_GEN_LOCK = asyncio.Lock()  # the Pi runs one generation at a time; serialize them


def _prune_jobs() -> None:
    if len(_JOBS) > _JOBS_MAX:
        for key in list(_JOBS)[:-_JOBS_MAX]:
            _JOBS.pop(key, None)

GOALS = {
    "strength": "maximal strength — heavy compound lifts, 3–6 reps, long rest",
    "hypertrophy": "muscle growth — 8–15 reps, moderate load, controlled tempo",
    "balance": "correct imbalances — prioritise the athlete's under-trained muscle groups",
    "general": "well-rounded general fitness — mix of compound and accessory work",
}

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
                            "minItems": 4,
                            "maxItems": 7,
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


# ── Training-profile builder ─────────────────────────────────────────

async def build_profile(conn: aiosqlite.Connection, uid: int) -> dict:
    """Summarise the user's recent training for the coach prompt."""
    # Frequency / span (finished workouts only).
    async with conn.execute(
        """
        SELECT COUNT(*) AS n,
               MIN(DATE(started_at)) AS first_day,
               MAX(DATE(started_at)) AS last_day
        FROM workouts
        WHERE user_id = ? AND ended_at IS NOT NULL
        """,
        (uid,),
    ) as cur:
        freq = dict(await cur.fetchone())

    # Most-trained movements over the last 90 days.
    async with conn.execute(
        """
        SELECT e.name, e.category, COUNT(*) AS sets
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-90 days')
        GROUP BY e.id
        ORDER BY sets DESC
        LIMIT 15
        """,
        (uid,),
    ) as cur:
        top_exercises = [dict(r) for r in await cur.fetchall()]

    # Set count per primary muscle (coverage signal) over 90 days.
    async with conn.execute(
        """
        SELECT em.muscle, COUNT(*) AS sets
        FROM sets s
        JOIN exercise_muscles em ON em.exercise_id = s.exercise_id AND em.is_primary = 1
        JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ?
          AND DATE(w.started_at) >= DATE('now', '-90 days')
        GROUP BY em.muscle
        ORDER BY sets DESC
        """,
        (uid,),
    ) as cur:
        muscle_sets = {r["muscle"]: r["sets"] for r in await cur.fetchall()}

    # Top estimated 1RMs (Epley) for loaded lifts.
    async with conn.execute(
        """
        SELECT e.name, MAX(ROUND(s.weight_kg * (1 + s.reps / 30.0), 1)) AS e1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ? AND s.weight_kg > 0
        GROUP BY s.exercise_id
        ORDER BY e1rm DESC
        LIMIT 8
        """,
        (uid,),
    ) as cur:
        top_lifts = [dict(r) for r in await cur.fetchall()]

    # Sessions per week estimate.
    sessions_per_week = None
    if freq["n"] and freq["first_day"] and freq["last_day"]:
        async with conn.execute(
            "SELECT MAX(1, (JULIANDAY(?) - JULIANDAY(?)) / 7.0) AS weeks",
            (freq["last_day"], freq["first_day"]),
        ) as cur:
            weeks = (await cur.fetchone())["weeks"] or 1
        sessions_per_week = round(freq["n"] / weeks, 1)

    # Under-trained muscles = library muscles with the fewest logged sets.
    async with conn.execute(
        "SELECT DISTINCT muscle FROM exercise_muscles WHERE is_primary = 1 ORDER BY muscle"
    ) as cur:
        all_muscles = [r["muscle"] for r in await cur.fetchall()]
    undertrained = sorted(all_muscles, key=lambda m: muscle_sets.get(m, 0))[:4]

    return {
        "total_workouts": freq["n"],
        "first_day": freq["first_day"],
        "last_day": freq["last_day"],
        "sessions_per_week": sessions_per_week,
        "top_exercises": top_exercises,
        "muscle_sets": muscle_sets,
        "undertrained": undertrained,
        "top_lifts": top_lifts,
    }


# Equipment ranked by how "staple" it is — biases the shortlist toward
# compound barbell/dumbbell work over isolation machines.
_EQUIP_RANK = {"Barbell": 0, "Dumbbell": 1, "Bodyweight": 2, "Cable": 3, "Machine": 4}
_PER_CATEGORY = 8  # cap names per category to keep the prompt small enough for on-device inference


async def _exercise_catalog(conn: aiosqlite.Connection, uid: int) -> dict[str, list[str]]:
    """
    A focused, capped shortlist of pickable exercises grouped by category
    (Cardio excluded). Movements the athlete already trains rank first, then
    barbell/dumbbell staples. Kept small so the prompt fits the time budget of
    a local model — generated names are still validated against the full
    library at save time, so nothing here limits what can ultimately be stored.
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
        0 if r["used"] else 1,                 # exercises the user trains come first
        _EQUIP_RANK.get(r["equipment"], 5),    # then staple equipment
        r["name"],
    ))

    catalog: dict[str, list[str]] = {}
    for r in rows:
        bucket = catalog.setdefault(r["category"], [])
        if len(bucket) < _PER_CATEGORY:
            bucket.append(r["name"])
    return catalog


def _build_prompt(goal: str, days: int, profile: dict, catalog: dict, focus_note: str) -> str:
    """Render the human-readable context block handed to the model."""
    lines: list[str] = []
    lines.append(f"GOAL: {GOALS[goal]}")
    lines.append(f"TRAINING DAYS PER WEEK: {days}")
    if focus_note.strip():
        lines.append(f"ATHLETE REQUEST: {focus_note.strip()}")
    lines.append("")

    lines.append("ATHLETE TRAINING PROFILE (last 90 days):")
    if profile["total_workouts"]:
        spw = profile["sessions_per_week"]
        lines.append(
            f"- {profile['total_workouts']} sessions logged"
            + (f", ~{spw}/week" if spw else "")
        )
    else:
        lines.append("- No workout history yet — treat as a beginner.")

    if profile["top_exercises"]:
        movers = ", ".join(
            f"{e['name']} ({e['sets']} sets)" for e in profile["top_exercises"][:10]
        )
        lines.append(f"- Most-trained movements: {movers}")
    if profile["muscle_sets"]:
        cov = ", ".join(f"{m}:{n}" for m, n in profile["muscle_sets"].items())
        lines.append(f"- Sets per muscle: {cov}")
    if profile["undertrained"]:
        lines.append(f"- Under-trained / neglected: {', '.join(profile['undertrained'])}")
    if profile["top_lifts"]:
        lifts = ", ".join(f"{l['name']} e1RM {l['e1rm']}kg" for l in profile["top_lifts"])
        lines.append(f"- Estimated 1RMs: {lifts}")
    lines.append("")

    lines.append("ALLOWED EXERCISES (use these EXACT names only):")
    for cat, names in catalog.items():
        lines.append(f"- {cat}: {', '.join(names)}")
    lines.append("")

    lines.append(
        f"Design a {days}-day weekly training split for this athlete. "
        f"Return exactly {days} day(s). Each day needs a short focus label "
        "(e.g. 'Push', 'Lower Body', 'Full Body') and 4–7 exercises. "
        "For every exercise give sets (integer) and a rep target (e.g. '8-12' "
        "or '5'), plus a brief coaching note. Favour movements the athlete "
        "already trains, but deliberately add work for under-trained muscles. "
        "Only use exercise names from the ALLOWED list above."
    )
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You are an elite strength & conditioning coach building a personalised "
    "weekly training routine. You reason from the athlete's real logged history. "
    "You only prescribe exercises from the provided allowed list, using their "
    "exact names. You return your plan strictly as JSON matching the requested "
    "schema — no prose outside the JSON."
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

@router.get("/coach", response_class=HTMLResponse)
async def coach_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    profile = await build_profile(conn, uid)
    available, models = await ollama.is_available()

    async with conn.execute(
        """
        SELECT id, title, goal, days_per_week, plan_json, model, created_at
        FROM coach_plans
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (uid,),
    ) as cur:
        saved = []
        for r in await cur.fetchall():
            d = dict(r)
            try:
                d["plan"] = json.loads(d.pop("plan_json"))
            except (json.JSONDecodeError, TypeError):
                d["plan"] = {"days": []}
            saved.append(d)

    return templates.TemplateResponse(request, "coach.html", {
        "user": dict(current_user),
        "profile": profile,
        "saved_plans": saved,
        "ollama_available": available,
        "ollama_models": models,
        "ollama_model": ollama.ollama_model(),
        "goals": GOALS,
    })


async def _run_generation(
    job_id: str, conn: aiosqlite.Connection, uid: int,
    goal: str, days: int, focus_note: str,
) -> None:
    """Background worker: build the profile, ask Ollama, validate, store result.

    Serialized by _GEN_LOCK so concurrent jobs can't thrash the Pi's CPU (which
    makes every generation crawl past the timeout)."""
    try:
        async with _GEN_LOCK:
            profile = await build_profile(conn, uid)
            catalog = await _exercise_catalog(conn, uid)
            prompt = _build_prompt(goal, days, profile, catalog, focus_note)
            raw = await ollama.chat_json(
                _SYSTEM_PROMPT, prompt, _plan_schema(days), timeout=480.0
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

    # Single-flight: if this user already has a generation running, hand back the
    # same job id. Repeated clicks (or a stale tab) then attach to the one job
    # instead of spawning several that would thrash the Pi.
    existing = _ACTIVE_BY_USER.get(uid)
    if existing and _JOBS.get(existing, {}).get("status") == "pending":
        return JSONResponse({"job_id": existing}, status_code=202)

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {"status": "pending", "user_id": uid}
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
    if job["status"] == "pending":
        return JSONResponse({"status": "pending"})
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
    await conn.execute("DELETE FROM coach_plans WHERE id = ?", (plan_id,))
    await conn.commit()
