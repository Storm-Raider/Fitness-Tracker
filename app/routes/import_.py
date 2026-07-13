import csv
import io
import logging
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, UploadFile

import app.db
from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.csv_utils import get_or_create_exercise

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
MAX_ROWS = 50_000

# Distinguishing columns for each supported format.
# Exercise Name / Weight / Reps are common to both.
_STRONG_MARKER_COLS = {"Workout Name", "Date", "Exercise Name", "Weight", "Reps"}
_HEVY_MARKER_COLS = {"Title", "Start Time", "Exercise Name", "Weight", "Reps"}


def _lbs_to_kg(value: float) -> float:
    return round(value * 0.453592, 2)


def _detect_format(fieldnames: list[str] | None) -> str | None:
    cols = set(fieldnames or [])
    if _STRONG_MARKER_COLS.issubset(cols):
        return "strong"
    if _HEVY_MARKER_COLS.issubset(cols):
        return "hevy"
    return None


def _workout_key(row: dict, fmt: str) -> tuple[str, str]:
    """Return (date_str, name) used to group consecutive rows into one workout."""
    if fmt == "hevy":
        raw_dt = (row.get("Start Time") or "").strip()
        return raw_dt[:10], (row.get("Title") or "Imported Workout").strip()
    # strong
    return (row.get("Date") or "").strip(), (row.get("Workout Name") or "Imported Workout").strip()


@router.post("/import/csv")
async def import_csv(
    file: UploadFile,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 10MB limit")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    fmt = _detect_format(reader.fieldnames)
    if fmt is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unrecognized CSV format. "
                "Strong requires columns: Date, Workout Name, Exercise Name, Weight, Reps. "
                "Hevy requires columns: Start Time, Title, Exercise Name, Weight, Reps."
            ),
        )

    rows = list(reader)
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"File exceeds {MAX_ROWS:,} row limit")

    imported = 0
    skipped = 0

    async with app.db.write_lock:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            current_workout_id: int | None = None
            current_workout_key: str | None = None

            for row in rows:
                exercise_name = (row.get("Exercise Name") or "").strip()
                weight_raw = (row.get("Weight") or "").strip()
                reps_raw = (row.get("Reps") or "").strip()

                if not exercise_name or not weight_raw or not reps_raw:
                    skipped += 1
                    continue

                try:
                    weight = float(weight_raw)
                    reps = int(float(reps_raw))
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Non-numeric weight or reps in row: {dict(row)}",
                    )

                weight_unit = (row.get("Weight Unit") or "kg").strip().lower()
                if weight_unit == "lbs":
                    weight = _lbs_to_kg(weight)

                workout_date, workout_name = _workout_key(row, fmt)
                row_key = f"{workout_date}:{workout_name}"

                if row_key != current_workout_key:
                    started_at = workout_date or datetime.now().isoformat()
                    async with conn.execute(
                        "INSERT INTO workouts(started_at, ended_at, notes, user_id) "
                        "VALUES (?, ?, ?, ?)",
                        (started_at, started_at, f"Imported: {workout_name}", uid),
                    ) as cur:
                        current_workout_id = cur.lastrowid
                    current_workout_key = row_key

                exercise_id = await get_or_create_exercise(conn, exercise_name)
                set_notes = (row.get("Notes") or "").strip() or None

                await conn.execute(
                    "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (current_workout_id, exercise_id, reps, weight, set_notes, uid),
                )
                imported += 1

            await conn.execute("COMMIT")
        except HTTPException:
            await conn.execute("ROLLBACK")
            raise
        except Exception as exc:
            await conn.execute("ROLLBACK")
            logger.exception("CSV import failed: %s", exc)
            raise HTTPException(status_code=500, detail="Import failed — transaction rolled back")

    return {"imported": imported, "skipped": skipped}
