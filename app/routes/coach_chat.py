"""AI coach chat: read the conversation, send a message, undo, durable notes.

Contract: docs/superpowers/specs/2026-10-04-coach-chat-design.md. The rules about what a
turn *means* (patching, diffing, notes, history) are pure and live in
app/utils/coach_chat.py; quota and concurrency state in app/utils/coach_budget.py. This
module is the glue: gate the request, call Gemini with no lock held, then commit
everything in one compare-and-set transaction.

Every error is {"detail": <copy to show>, "kind": <machine-readable>} so the UI can pick
its wording by kind.
"""
import json
import logging
import time

import aiosqlite
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.db import WriteConflict, get_db, write_tx
from app.routes.auth import get_current_user, require_admin
from app.utils import coach_budget, coach_chat, gemini
from app.utils.coach_plan import exercise_catalog, name_to_id_map
from app.utils.training_profile import build_profile

router = APIRouter()

TURN_BUDGET_SECONDS = 30.0      # end to end, from the first request to the last
ATTEMPT_TIMEOUT_SECONDS = 20.0  # one HTTP attempt
MIN_RETRY_SECONDS = 10.0        # a retry needs at least this much of the budget left
HISTORY_LIMIT = 100             # messages returned to the client

GOAL_LABELS = {
    "strength": "build maximal strength",
    "hypertrophy": "build muscle (hypertrophy)",
    "balance": "correct imbalances and under-trained muscles",
    "general": "well-rounded general fitness",
}


class ChatError(Exception):
    """An expected failure: turned into {"detail", "kind"} with `status` by app.main."""

    def __init__(self, status: int, kind: str, detail: str):
        super().__init__(detail)
        self.status, self.kind, self.detail = status, kind, detail


COPY = {
    "disabled": "Coach chat is off right now.",
    "not_configured": "The coach isn't set up. Ask the admin to set GEMINI_API_KEY.",
    "ack_required": "Please read and accept the privacy note first.",
    "replaced": "This plan was replaced. Reload to see the new one.",
    "stale": "This plan changed in another tab. Reload to see it.",
    "working": "Still working on your last message.",
    "quota": "Coach is resting until tomorrow. Undo and swaps still work.",
    "busy": "Coach is busy, try again in a moment.",
}


def _from_gemini(exc: gemini.GeminiError) -> ChatError:
    k = exc.kind
    if k == "not_configured":
        return ChatError(503, k, COPY["not_configured"])
    if k == "auth":
        return ChatError(503, k, "Coach is misconfigured. Tell the admin.")
    if k == "quota":
        return ChatError(429, k, COPY["quota"])
    if k == "rate_limited":
        return ChatError(429, k, COPY["busy"])
    if k == "blocked":
        return ChatError(502, k, "I can't help with that. Try rephrasing.")
    if k == "timeout":
        return ChatError(502, k, "The coach took too long. Your message is back in the box.")
    if k == "unreachable":
        return ChatError(502, k, "No connection to the coach. Your message is back in the box.")
    return ChatError(502, k, "The coach had a problem. Try again.")


# ── Models ───────────────────────────────────────────────────────────

class ChatIn(BaseModel):
    message: str
    base_rev: int | None = None


class UndoIn(BaseModel):
    base_rev: int


class NoteIn(BaseModel):
    text: str
    source_plan_id: int | None = None


# ── Data helpers ─────────────────────────────────────────────────────

def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


async def _plan_row(conn: aiosqlite.Connection, plan_id: int, uid: int):
    async with conn.execute(
        "SELECT id, title, goal, days_per_week, plan_json, status, rev, undo_json, feedback "
        "FROM coach_plans WHERE id = ? AND user_id = ?", (plan_id, uid),
    ) as cur:
        return await cur.fetchone()


def _plan_of(row) -> dict:
    plan = _loads(row["plan_json"], None)
    if not isinstance(plan, dict) or not isinstance(plan.get("days"), list) or not plan["days"]:
        raise ChatError(409, "unusable", "This plan can't be edited in chat. Generate a new one.")
    plan.setdefault("goal", row["goal"])
    return plan


async def _notes(conn: aiosqlite.Connection, uid: int) -> list[dict]:
    async with conn.execute(
        "SELECT id, text, created_at FROM coach_notes WHERE user_id = ? ORDER BY id", (uid,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _messages(conn: aiosqlite.Connection, plan_id: int, uid: int, limit: int) -> list[dict]:
    async with conn.execute(
        "SELECT id, role, content, changed_days, changes, undone, created_at FROM coach_messages "
        "WHERE plan_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?", (plan_id, uid, limit),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    for r in rows:
        r["changed_days"] = _loads(r["changed_days"], [])
        r["changes"] = _loads(r["changes"], [])
        r["undone"] = bool(r["undone"])
    return rows


async def _acked(conn: aiosqlite.Connection, uid: int) -> bool:
    async with conn.execute("SELECT coach_chat_ack_at FROM user_settings WHERE user_id = ?", (uid,)) as cur:
        row = await cur.fetchone()
    return bool(row and row["coach_chat_ack_at"])


def _undo_info(undo_json) -> dict:
    stack = _loads(undo_json, [])
    top = stack[-1] if isinstance(stack, list) and stack else None
    return {"has_undo": bool(top), "undo_label": (top or {}).get("label") if isinstance(top, dict) else None}


# ── Reading ──────────────────────────────────────────────────────────

@router.get("/coach/plans/{plan_id}/chat")
async def get_chat(plan_id: int, conn: aiosqlite.Connection = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """Everything the panel needs to open: the plan with its rev, whether it can be edited,
    the saved conversation and the athlete's notes. Also how a saved plan opens read-only."""
    uid = current_user["id"]
    row = await _plan_row(conn, plan_id, uid)
    if not row:
        return JSONResponse({"detail": "Plan not found", "kind": "not_found"}, status_code=404)
    await coach_budget.ensure_loaded(conn)
    plan = _plan_of(row)
    return {
        "plan": plan, "plan_id": row["id"], "rev": row["rev"], "status": row["status"],
        "can_edit": row["status"] == "draft",
        "messages": await _messages(conn, plan_id, uid, HISTORY_LIMIT),
        "notes": await _notes(conn, uid), "note_cap": coach_chat.NOTE_CAP,
        "feedback": row["feedback"],
        "enabled": coach_budget.chat_enabled() and gemini.is_configured(),
        "at_cap": coach_budget.at_cap(),
        "acked": await _acked(conn, uid),
        "max_message_chars": coach_chat.MAX_MESSAGE_CHARS,
        **_undo_info(row["undo_json"]),
    }


# ── A chat turn ──────────────────────────────────────────────────────

@router.post("/coach/plans/{plan_id}/chat")
async def chat_turn(plan_id: int, body: ChatIn, conn: aiosqlite.Connection = Depends(get_db),
                    current_user=Depends(get_current_user)):
    uid = current_user["id"]
    started = time.monotonic()
    outcome, kind, changed = "error", "", []
    token = None
    requests_made = 0

    async def counted_request():
        nonlocal requests_made
        await coach_budget.on_request()
        requests_made += 1
    try:
        # ── 1. Gate: nothing below costs a model request ─────────────
        if not coach_budget.chat_enabled():
            raise ChatError(503, "disabled", COPY["disabled"])
        if not gemini.is_configured():
            raise ChatError(503, "not_configured", COPY["not_configured"])
        try:
            message = coach_chat.validate_message(body.message)
        except ValueError as exc:
            raise ChatError(422, "invalid", str(exc))
        if not await _acked(conn, uid):
            raise ChatError(403, "ack_required", COPY["ack_required"])
        row = await _plan_row(conn, plan_id, uid)
        if not row:
            raise ChatError(409, "replaced", COPY["replaced"])
        editable = row["status"] == "draft"
        if editable and body.base_rev is None:
            raise ChatError(422, "invalid", "Reload the page and try again.")
        if editable and body.base_rev != row["rev"]:
            raise ChatError(409, "stale", COPY["stale"])
        plan = _plan_of(row)
        await coach_budget.ensure_loaded(conn)
        if coach_budget.at_cap():
            raise ChatError(429, "quota", COPY["quota"])

        try:
            with coach_budget.single_flight(uid):
                async with coach_budget.chat_slot():
                    # ── 2. Context, built fresh; no lock is held from here to the model's answer ──
                    profile = await build_profile(conn, uid, fresh=True)
                    notes = [n["text"] for n in await _notes(conn, uid)]
                    catalog = await exercise_catalog(conn, uid, profile.get("preferred_equipment"))
                    history = await _messages(conn, plan_id, uid, coach_chat.HISTORY_MESSAGES)
                    contents = coach_chat.build_contents(
                        context_text=coach_chat.context_text(
                            profile, plan["goal"], GOAL_LABELS.get(plan["goal"], plan["goal"]), catalog, notes),
                        history=history, plan=plan, message=message, profile=profile)
                    # ── 3. One model request (at most two in all) within the turn budget ──
                    raw = await gemini.chat_turn_json(
                        coach_chat.CHAT_SYSTEM_PROMPT, contents, coach_chat.reply_schema(len(plan["days"])),
                        temperature=0.3, timeout=ATTEMPT_TIMEOUT_SECONDS,
                        deadline=time.monotonic() + TURN_BUDGET_SECONDS,
                        on_request=counted_request, model=gemini.chat_model(),
                        max_attempts=2, max_requests=2, retry_timeouts=False, retry_malformed=True,
                        min_retry_seconds=MIN_RETRY_SECONDS)
        except coach_budget.AlreadyWorking:
            raise ChatError(409, "working", COPY["working"])
        except coach_budget.ChatBusy:
            raise ChatError(429, "busy", COPY["busy"])
        except coach_budget.DailyCapReached:
            raise ChatError(429, "quota", COPY["quota"])
        except gemini.GeminiError as exc:
            raise _from_gemini(exc)
        try:
            reply = coach_chat.parse_reply(raw)
        except ValueError:
            raise ChatError(502, "malformed", "The coach had a problem. Try again.")

        # ── 4. Apply deterministically (drafts only) ─────────────────
        result = None
        reply_text = reply.text
        if editable and reply.days:
            name_map, norm_map = await name_to_id_map(conn)
            result = coach_chat.apply_patch(plan, reply.days, name_map, norm_map)
            extra = coach_chat.unapplied_note(result)
            if extra:
                reply_text = f"{reply_text} {extra}"[: coach_chat.MAX_REPLY_CHARS + 200]
        elif reply.days:
            logging.info("coach_chat: ignored %d day patch(es) on saved plan %d", len(reply.days), plan_id)
        changed = result.changed_days if result else []
        new_plan = result.plan if result and changed else None

        # ── 5. One transaction: CAS first, then messages, plan, undo, counter ─
        async with write_tx(conn):
            if editable:
                async with conn.execute(
                    "UPDATE coach_plans SET plan_json = COALESCE(?, plan_json), "
                    "rev = rev + ?, updated_at = datetime('now','localtime') "
                    "WHERE id = ? AND user_id = ? AND status = 'draft' AND rev = ?",
                    (json.dumps(new_plan) if new_plan else None, 1 if new_plan else 0,
                     plan_id, uid, body.base_rev),
                ) as cur:
                    if cur.rowcount == 0:
                        raise WriteConflict(COPY["stale"], kind="stale")
            else:
                async with conn.execute("SELECT 1 FROM coach_plans WHERE id = ? AND user_id = ?", (plan_id, uid)) as cur:
                    if not await cur.fetchone():
                        raise WriteConflict(COPY["replaced"], kind="replaced")
            async with conn.execute(
                "INSERT INTO coach_messages(plan_id, user_id, role, content) VALUES (?, ?, 'user', ?)",
                (plan_id, uid, message),
            ) as cur:
                user_msg_id = cur.lastrowid
            async with conn.execute(
                "INSERT INTO coach_messages(plan_id, user_id, role, content, changed_days, changes) "
                "VALUES (?, ?, 'model', ?, ?, ?)",
                (plan_id, uid, reply_text,
                 json.dumps(changed) if changed else None,
                 json.dumps(result.changes) if changed else None),
            ) as cur:
                model_msg_id = cur.lastrowid
            undo_json = row["undo_json"]
            if new_plan:
                label = "Edited " + ", ".join(f"Day {d}" for d in changed)
                undo_json = coach_chat.push_undo(row["undo_json"], plan, label, model_msg_id)
                await conn.execute("UPDATE coach_plans SET undo_json = ? WHERE id = ?", (undo_json, plan_id))
            token = await coach_budget.persist(conn)
        coach_budget.confirm(token)

        # ── 6. Respond ───────────────────────────────────────────────
        existing = await _notes(conn, uid)
        proposed = reply.propose_note
        if proposed and any(n["text"].lower() == proposed.lower() for n in existing):
            proposed = None                                   # already remembered: nothing to ask
        feedback = {"value": reply.feedback, "current": row["feedback"]} if reply.feedback else None
        outcome = "ok" if changed else "no_change"
        return {
            "reply": reply_text,
            "plan": new_plan or plan, "rev": row["rev"] + (1 if new_plan else 0),
            "changed_days": changed, "changes": result.changes if changed else [],
            "propose_note": proposed,
            "notes_full": bool(proposed) and len(existing) >= coach_chat.NOTE_CAP,
            "feedback": feedback,
            "message_ids": {"user": user_msg_id, "model": model_msg_id},
            **_undo_info(undo_json),
        }
    except ChatError as exc:
        outcome, kind = "error", exc.kind
        raise
    except WriteConflict as exc:
        outcome, kind = "error", exc.kind
        raise
    finally:
        if token is None:
            await coach_budget.flush(conn)    # requests were counted even though nothing was saved
        coach_budget.record(outcome if not kind else f"error:{kind}")
        logging.info(
            "coach_chat outcome=%s kind=%s plan=%s uid=%s duration_ms=%d changed_days=%s requests=%d",
            outcome, kind or "-", plan_id, uid, (time.monotonic() - started) * 1000, changed,
            requests_made,
        )


# ── Undo ─────────────────────────────────────────────────────────────

@router.post("/coach/plans/{plan_id}/undo")
async def undo_edit(plan_id: int, body: UndoIn, conn: aiosqlite.Connection = Depends(get_db),
                    current_user=Depends(get_current_user)):
    """Restore the most recent pre-edit snapshot. Needs no model call, so it keeps working
    at the daily cap and with the kill switch on."""
    uid = current_user["id"]
    row = await _plan_row(conn, plan_id, uid)
    if not row:
        raise ChatError(409, "replaced", COPY["replaced"])
    if row["status"] != "draft":
        raise ChatError(409, "saved", "Chat and swap edit drafts; use Regenerate.")
    if body.base_rev != row["rev"]:
        raise ChatError(409, "stale", COPY["stale"])
    try:
        entry, rest = coach_chat.pop_undo(row["undo_json"])
    except coach_chat.CorruptUndo as exc:
        if "nothing to undo" in str(exc):
            raise ChatError(409, "nothing_to_undo", "There is nothing to undo.")
        logging.warning("coach_chat: corrupt undo entry on plan %d: %s", plan_id, exc)
        raise ChatError(409, "corrupt", "Can't restore this edit.")
    async with conn.execute("SELECT id FROM exercises") as cur:
        existing_ids = {r["id"] for r in await cur.fetchall()}
    restored, missing = coach_chat.drop_missing_exercises(entry["plan_json"], existing_ids)
    async with write_tx(conn):
        async with conn.execute(
            "UPDATE coach_plans SET plan_json = ?, undo_json = ?, rev = rev + 1, "
            "updated_at = datetime('now','localtime') "
            "WHERE id = ? AND user_id = ? AND status = 'draft' AND rev = ?",
            (json.dumps(restored), rest, plan_id, uid, body.base_rev),
        ) as cur:
            if cur.rowcount == 0:
                raise WriteConflict(COPY["stale"], kind="stale")
        if entry.get("message_id"):
            await conn.execute(
                "UPDATE coach_messages SET undone = 1 WHERE id = ? AND plan_id = ?",
                (entry["message_id"], plan_id))
    notice = ("Dropped exercises that no longer exist: " + ", ".join(dict.fromkeys(missing)) + ".") if missing else ""
    return {"plan": restored, "rev": row["rev"] + 1, "notice": notice, "undone_message_id": entry.get("message_id"),
            **_undo_info(rest)}


# ── Durable notes ────────────────────────────────────────────────────

@router.get("/coach/notes")
async def list_notes(conn: aiosqlite.Connection = Depends(get_db), current_user=Depends(get_current_user)):
    return {"notes": await _notes(conn, current_user["id"]), "cap": coach_chat.NOTE_CAP}


@router.post("/coach/notes", status_code=201)
async def add_note(body: NoteIn, conn: aiosqlite.Connection = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """Save a note the athlete confirmed. Duplicates (any case) are quietly accepted."""
    uid = current_user["id"]
    if not coach_budget.chat_enabled():
        raise ChatError(503, "disabled", COPY["disabled"])
    text = coach_chat.clean_note(body.text)
    if not text:
        raise ChatError(422, "invalid", "A note needs some text.")
    source = None
    if body.source_plan_id is not None:
        source = body.source_plan_id if await _plan_row(conn, body.source_plan_id, uid) else None
    async with write_tx(conn):
        async with conn.execute(
            "SELECT id, text, created_at FROM coach_notes WHERE user_id = ? AND text = ? COLLATE NOCASE",
            (uid, text),
        ) as cur:
            dup = await cur.fetchone()
        if dup:
            return JSONResponse(dict(dup), status_code=200)
        async with conn.execute("SELECT COUNT(*) AS n FROM coach_notes WHERE user_id = ?", (uid,)) as cur:
            if (await cur.fetchone())["n"] >= coach_chat.NOTE_CAP:
                raise ChatError(409, "notes_full", coach_chat.NOTES_FULL_MESSAGE)
        async with conn.execute(
            "INSERT INTO coach_notes(user_id, text, source_plan_id) VALUES (?, ?, ?)", (uid, text, source),
        ) as cur:
            note_id = cur.lastrowid
        async with conn.execute("SELECT id, text, created_at FROM coach_notes WHERE id = ?", (note_id,)) as cur:
            return dict(await cur.fetchone())


@router.delete("/coach/notes/{note_id}", status_code=204)
async def delete_note(note_id: int, conn: aiosqlite.Connection = Depends(get_db),
                      current_user=Depends(get_current_user)):
    async with write_tx(conn):
        async with conn.execute(
            "DELETE FROM coach_notes WHERE id = ? AND user_id = ?", (note_id, current_user["id"]),
        ) as cur:
            if cur.rowcount == 0:
                raise ChatError(404, "not_found", "Note not found.")
    return Response(status_code=204)


# ── Privacy acknowledgement and admin usage ──────────────────────────

@router.post("/coach/chat/ack", status_code=204)
async def ack_privacy(conn: aiosqlite.Connection = Depends(get_db), current_user=Depends(get_current_user)):
    """The athlete accepted that chat messages and notes are sent to Google."""
    async with write_tx(conn):
        await conn.execute(
            "INSERT INTO user_settings(user_id, coach_chat_ack_at) VALUES (?, datetime('now','localtime')) "
            "ON CONFLICT(user_id) DO UPDATE SET coach_chat_ack_at = excluded.coach_chat_ack_at",
            (current_user["id"],),
        )
    return Response(status_code=204)


@router.get("/coach/usage")
async def usage(conn: aiosqlite.Connection = Depends(get_db), admin=Depends(require_admin)):
    """Admin only: today's Gemini request count against the cap, and outcome counters since boot."""
    await coach_budget.ensure_loaded(conn)
    return coach_budget.report()
