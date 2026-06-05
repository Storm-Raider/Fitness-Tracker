"""
Unified plan page — merges AI Coach and Mesocycle Planner into one /plan route.

GET /plan renders plan.html with context from both planning modes.
Write endpoints (POST /coach/generate, POST /planner/plans, etc.) stay
on their original routes in coach.py and planner.py.
"""


import json as _json

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import ollama
from app.utils.render import render
from app.utils.training_profile import build_profile

router = APIRouter()


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    # Training profile (for AI mode pills + prompt context)
    profile = await build_profile(conn, uid)

    # Top e1RM lifts for mesocycle lift table pre-population
    async with conn.execute(
        """
        SELECT e.name, MAX(ROUND(s.weight_kg*(1+s.reps/30.0),1)) AS e1rm
        FROM sets s
        JOIN exercises e ON e.id=s.exercise_id
        WHERE s.user_id=? AND e.category NOT IN ('Cardio')
        GROUP BY s.exercise_id
        ORDER BY e1rm DESC
        LIMIT 20
        """,
        (uid,),
    ) as c:
        top_lifts = [dict(r) for r in await c.fetchall()]

    # Saved AI coach plans
    async with conn.execute(
        """
        SELECT id, title, goal, days_per_week, model, created_at, plan_json, feedback
        FROM coach_plans
        WHERE user_id=? ORDER BY created_at DESC LIMIT 10
        """,
        (uid,),
    ) as c:
        coach_plans = []
        for r in await c.fetchall():
            p = dict(r)
            # Parse plan_json to get routine_ids (new plans have it; legacy plans don't)
            plan_data = {}
            try:
                plan_data = _json.loads(p.pop("plan_json") or "{}")
            except (ValueError, TypeError):
                pass

            if "routine_ids" in plan_data:
                p["routine_ids"] = plan_data["routine_ids"]
            else:
                # Legacy fallback: query routines matching "{title} · Day %"
                async with conn.execute(
                    "SELECT id FROM routines WHERE user_id=? AND name LIKE ? ORDER BY id",
                    (uid, f"{p['title']} · Day %"),
                ) as rc:
                    p["routine_ids"] = [row["id"] for row in await rc.fetchall()]

            coach_plans.append(p)

    # Saved mesocycle plans
    async with conn.execute(
        "SELECT id, name, goal, weeks, created_at FROM mesocycle_plans WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
        (uid,),
    ) as c:
        meso_plans = [dict(r) for r in await c.fetchall()]

    # Merged saved plans panel — normalise column name mismatch:
    # coach_plans.title vs mesocycle_plans.name → both become display_name
    saved_plans = sorted(
        [{"type": "ai",   "display_name": p["title"], **p} for p in coach_plans] +
        [{"type": "meso", "display_name": p["name"],  **p} for p in meso_plans],
        key=lambda p: p["created_at"],
        reverse=True,
    )[:15]

    available, models = await ollama.is_available()

    return render(
        request,
        "plan",
        {
            "user": dict(current_user),
            "profile": profile,
            "top_lifts": top_lifts,
            "saved_plans": saved_plans,
            "ollama_available": available,
            "ollama_models": models,
            "ollama_model": ollama.ollama_model(),
        },
    )
