import csv
import io
import logging
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.db import get_db
from app.utils.csv_utils import get_or_create_exercise

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB

# Strong CSV columns (subset we care about)
# Date, Workout Name, Duration, Exercise Name, Set Order, Weight, Reps,
# Distance, Seconds, Notes, Workout Notes, RPE, Weight Unit
_REQUIRED_COLS = {"Exercise Name", "Weight", "Reps"}


def _lbs_to_kg(value: float) -> float:
    return round(value * 0.453592, 2)


@router.post("/import/csv")
async def import_csv(
    file: UploadFile,
    conn: aiosqlite.Connection = Depends(get_db),
):
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 10MB limit")

    try:
        text = raw.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="File must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not _REQUIRED_COLS.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=422,
            detail=f"CSV must contain columns: {', '.join(sorted(_REQUIRED_COLS))}",
        )

    imported = 0
    skipped = 0
    rows = list(reader)

    # Atomic import: all rows or none
    await conn.execute("BEGIN IMMEDIATE")
    try:
        current_workout_id: int | None = None
        current_workout_key: str | None = None  # date + workout name

        for row in rows:
            exercise_name = (row.get("Exercise Name") or "").strip()
            weight_raw = (row.get("Weight") or "").strip()
            reps_raw = (row.get("Reps") or "").strip()

            # Skip cardio rows (no exercise name, weight, or reps)
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

            # Convert lbs to kg
            weight_unit = (row.get("Weight Unit") or "kg").strip().lower()
            if weight_unit == "lbs":
                weight = _lbs_to_kg(weight)

            # Create a workout per unique (date, workout name) combination
            workout_date = (row.get("Date") or "").strip()
            workout_name = (row.get("Workout Name") or "Imported Workout").strip()
            row_key = f"{workout_date}:{workout_name}"

            if row_key != current_workout_key:
                started_at = workout_date or datetime.now().isoformat()
                async with conn.execute(
                    "INSERT INTO workouts(started_at, ended_at, notes, user_id) "
                    "VALUES (?, ?, ?, 1)",
                    (started_at, started_at, f"Imported: {workout_name}"),
                ) as cur:
                    current_workout_id = cur.lastrowid
                current_workout_key = row_key

            exercise_id = await get_or_create_exercise(conn, exercise_name)
            set_notes = (row.get("Notes") or "").strip() or None

            await conn.execute(
                "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (current_workout_id, exercise_id, reps, weight, set_notes),
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
