
## GBrain Search Guidance

A knowledge graph of this codebase lives at `graphify-out/graph.json` (1,525 nodes, 2,707 edges, 122 communities). Use it for architecture questions, tracing call paths, and understanding cross-file relationships.

**When to use the graph (invoke `/graphify query "<question>"`):**
- "How does X work?" / "What calls Y?" / "Where is Z implemented?"
- Any question that crosses multiple files or route boundaries
- Tracing data flow end-to-end (route → DB → template)

**Key communities** (what lives where):
- `Challenge & Routine Routes` — `app/routes/challenges.py`, `app/routes/routines.py`
- `Challenge Logic (utils)` — `app/utils/challenges.py` (streak evaluation, reset logic)
- `Dashboard & Achievements Routes` — `app/routes/dashboard.py`, `app/routes/achievements.py`
- `Authentication Route` — `app/routes/auth.py` (HMAC session tokens, invite gate)
- `DB Init & Auth Core` — `app/db.py`, migration list, `init_db()`
- `DB Layer & Utility Routes` — `get_db()` dependency used by all 16 routes; only `require_owns()` is a shared DB utility
- `AI Coach Route` — `app/routes/coach.py`, `app/utils/ollama.py` (local Ollama, fully on-device)
- `Base Template & Design System` — `app/templates/base.html`, global unit toggles (`unitchange`, `distancechange`, `bodyunitchange` events)
- `Trash / Undo Utility` — `app/utils/trash.py`, `deleted_items` table, 7-day auto-purge
- `App Core & Middleware` — `app/main.py`, `AuthMiddleware`, `lifespan()`

**Architecture note:** Every route imports `get_db` and `Depends` directly — there is no service layer. Business logic lives in `app/utils/` only when it's complex enough to test independently (challenges, trash, PRs, heatmap, charts, Ollama).

**To rebuild after significant code changes:**
```
/graphify --update
```

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore

## Design System

Always read DESIGN.md before making any visual or UI decisions.
Font choices, colors, spacing, aesthetic direction, and design principles are defined there.
Do not deviate without explicit user approval.
Key rules:
- Blue (`--accent`) = interactive chrome only. Gold (`--pr`) = earned achievement only. Never swap.
- No light mode — this is a documented position, not a missing feature.
- All numeric data uses JetBrains Mono via the `.num` class.
- Touch targets: primary actions ≥56px height, steppers ≥40px.
