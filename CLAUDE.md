
## GBrain Search Guidance — check this FIRST

Before grepping, exploring files, or spawning an Explore agent, query the knowledge graph first: `/graphify query "<question>"`. A knowledge graph of this codebase lives at `graphify-out/graph.json` (3,421 nodes, 6,863 edges, 212 communities; rebuilt 2026-10-12) — it's faster and more complete than ad-hoc search for anything touching architecture or cross-file relationships.

**Always query the graph first for:**
- "How does X work?" / "What calls Y?" / "Where is Z implemented?"
- Any question that crosses multiple files or route boundaries
- Tracing data flow end-to-end (route → DB → template)

Only fall back to manual grep/Explore if the graph doesn't answer the question (e.g. it's stale — see rebuild command below).

**Key communities** (what lives where):
- `Challenge & Routine Routes` — `app/routes/challenges.py`, `app/routes/routines.py`
- `Challenge Logic (utils)` — `app/utils/challenges.py` (streak evaluation, reset logic)
- `Dashboard & Achievements Routes` — `app/routes/dashboard.py`, `app/routes/achievements.py`
- `Authentication Route` — `app/routes/auth.py` (HMAC session tokens, invite gate)
- `DB Init & Auth Core` — `app/db.py`, migration list, `init_db()`
- `DB Layer & Utility Routes` — `get_db()` dependency used by all 16 routes; only `require_owns()` is a shared DB utility
- `AI Coach Route` — `app/routes/coach.py`, `app/utils/gemini.py` (Google AI Studio / Gemini API; needs `GEMINI_API_KEY`, prompt leaves the device)
- `Base Template & Design System` — `app/templates/base.html`, global unit toggles (`unitchange`, `distancechange`, `bodyunitchange` events)
- `Trash / Undo Utility` — `app/utils/trash.py`, `deleted_items` table, 7-day auto-purge
- `App Core & Middleware` — `app/main.py`, `AuthMiddleware`, `lifespan()`

**Architecture note:** Every route imports `get_db` and `Depends` directly — there is no service layer. Business logic lives in `app/utils/` only when it's complex enough to test independently (challenges, trash, PRs, heatmap, charts, Gemini).

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

## Deploy Configuration (configured by /setup-deploy)
- Platform: Local Raspberry Pi (systemd)
- Production URL: http://localhost:8000
- Deploy workflow: `sudo systemctl restart zenkai`
- Deploy status command: `systemctl is-active zenkai`
- Project type: Web app (FastAPI/uvicorn)
- Post-deploy health check: http://localhost:8000

### Custom deploy hooks
- Pre-merge: none
- Deploy trigger: `sudo systemctl restart zenkai`
- Deploy status: `systemctl is-active zenkai`
- Health check: `curl -sf http://localhost:8000 -o /dev/null`

## Design System

Read DESIGN.md before visual or UI work: it defines the fonts, colors, spacing, and
aesthetic direction (Daylight, adopted 2026-10-10). Ask the user before departing from it.
When reviewing or QA-ing UI, flag code that doesn't match DESIGN.md.
Key rules:
- Orange (`--primary`) = the thing to press, always a fill with ink text; never orange text.
- Amber (`--record`) = a personal record or achievement only.
- Light mode only (the user's choice, replacing the earlier dark-only rule).
- Numbers use Archivo condensed with tabular figures via the `.num` class.
- Touch targets: primary actions ≥56px, set ticks ≥44px; no text below 13px.
