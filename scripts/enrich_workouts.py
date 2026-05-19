#!/usr/bin/env python3
"""
Enrich finished workouts with exercise form cues and references via Claude.

Runs nightly at 3 AM via system cron (see cron/fitstorm-enrich):
  0 3 * * * /home/stormraider/Desktop/Git/Fitness-Tracker/scripts/enrich_workouts.py

Processes BATCH_SIZE (default 2) unenriched workouts per run.
Requires ANTHROPIC_API_KEY in the environment (set in the cron file).
"""

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("enrich_workouts")

REPO_ROOT   = Path(__file__).resolve().parent.parent
DB_PATH     = os.environ.get("DATABASE_PATH", str(REPO_ROOT / "data" / "fitness.db"))
BATCH_SIZE  = int(os.environ.get("ENRICH_BATCH", "2"))
MODEL       = "claude-haiku-4-5-20251001"
MAX_TOKENS  = 1500

PROMPT = """\
You are a certified strength & conditioning coach reviewing a training session.

The athlete completed these exercises today: {exercises}

For EACH exercise, return a JSON object with:
- "cues": list of 3 concise technique cues (action-oriented, ≤12 words each)
- "mistakes": list of 2 common errors to avoid (≤12 words each)
- "reference": one short bibliographic or authoritative reference (book, journal, or org)

Return ONLY valid JSON, no markdown, no prose outside the JSON:
{{
  "Exercise Name": {{
    "cues": ["...", "...", "..."],
    "mistakes": ["...", "..."],
    "reference": "..."
  }}
}}"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Ensure enrichments table exists (migration may not have run yet)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_enrichments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id   INTEGER NOT NULL UNIQUE REFERENCES workouts(id) ON DELETE CASCADE,
            form_info    TEXT    NOT NULL DEFAULT '{}',
            generated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    return conn


def unenriched_workouts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT w.id, w.started_at, w.ended_at
        FROM   workouts w
        WHERE  w.ended_at IS NOT NULL
          AND  NOT EXISTS (
                   SELECT 1 FROM workout_enrichments e WHERE e.workout_id = w.id
               )
        ORDER  BY w.started_at DESC
        LIMIT  ?
        """,
        (BATCH_SIZE,),
    ).fetchall()


def exercises_for_workout(conn: sqlite3.Connection, workout_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT e.name
        FROM   sets s
        JOIN   exercises e ON e.id = s.exercise_id
        WHERE  s.workout_id = ?
        ORDER  BY e.name
        """,
        (workout_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def call_claude(exercise_names: list[str]) -> dict:
    import anthropic  # imported here so the module load fails loudly if missing
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": PROMPT.format(exercises=", ".join(exercise_names)),
        }],
    )
    raw = msg.content[0].text.strip()
    # Strip any accidental markdown fencing
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def mark_done(conn: sqlite3.Connection, workout_id: int, form_info: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO workout_enrichments(workout_id, form_info)
        VALUES (?, ?)
        """,
        (workout_id, json.dumps(form_info)),
    )
    conn.commit()


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.error("ANTHROPIC_API_KEY is not set — aborting")
        sys.exit(1)

    conn = get_db()
    workouts = unenriched_workouts(conn)

    if not workouts:
        log.info("No unenriched workouts found — nothing to do")
        return

    log.info("Processing %d workout(s)", len(workouts))

    for row in workouts:
        wid = row["id"]
        date = row["started_at"][:10]
        exercises = exercises_for_workout(conn, wid)

        if not exercises:
            log.info("Workout %d (%s): no sets logged — skipping", wid, date)
            mark_done(conn, wid, {})
            continue

        log.info("Workout %d (%s): enriching %d exercise(s): %s",
                 wid, date, len(exercises), ", ".join(exercises))
        try:
            form_info = call_claude(exercises)
            mark_done(conn, wid, form_info)
            log.info("  → Done. %d exercise(s) enriched.", len(form_info))
        except json.JSONDecodeError as exc:
            log.error("  JSON parse error for workout %d: %s", wid, exc)
            mark_done(conn, wid, {})
        except Exception as exc:
            log.error("  API/DB error for workout %d: %s", wid, exc)

    conn.close()
    log.info("Finished.")


if __name__ == "__main__":
    main()
