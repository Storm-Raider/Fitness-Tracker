import csv
import io
import logging
import math
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.db import get_db, write_tx
from app.routes.auth import get_current_user
from app.utils.coach_plan import invalidate_exercise_caches
from app.utils.csv_utils import get_or_create_exercise

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
MAX_ROWS = 50_000
MAX_WEIGHT_KG = 1000.0  # mirrors SetIn.weight_kg
MAX_REPS = 999          # mirrors SetIn.reps

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

    try:
        async with write_tx(conn):
            # Snapshot how many sets already exist for this user, grouped by
            # (workout date, exercise, weight, reps). This is a *multiset* count
            # (not a plain existence check) so re-importing the same file skips
            # every row it already contains, while a workout that legitimately
            # has repeated identical straight sets (e.g. 3x100kg x5) still
            # imports correctly the first time — nothing is skipped until the
            # count of matching rows already in the DB is exhausted.
            existing_counts: dict[tuple[str, int, float, int], int] = {}
            async with conn.execute(
                "SELECT w.started_at, s.exercise_id, s.weight_kg, s.reps, COUNT(*) "
                "FROM sets s JOIN workouts w ON s.workout_id = w.id "
                "WHERE w.user_id = ? "
                "GROUP BY w.started_at, s.exercise_id, s.weight_kg, s.reps",
                (uid,),
            ) as cur:
                async for started_at, exercise_id, weight_kg, set_reps, cnt in cur:
                    existing_counts[(started_at, exercise_id, weight_kg, set_reps)] = cnt

            current_workout_id: int | None = None
            current_workout_key: str | None = None
            current_workout_date: str | None = None
            current_workout_name: str | None = None

            for row in rows:
                exercise_name = (row.get("Exercise Name") or "").strip()
                weight_raw = (row.get("Weight") or "").strip()
                reps_raw = (row.get("Reps") or "").strip()

                if not exercise_name or not weight_raw or not reps_raw:
                    skipped += 1
                    continue

                try:
                    weight = float(weight_raw)
                    reps_f = float(reps_raw)
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Non-numeric weight or reps in row: {dict(row)}",
                    )
                if not (math.isfinite(weight) and math.isfinite(reps_f)):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Weight or reps is not a finite number in row: {dict(row)}",
                    )
                reps = int(reps_f)

                weight_unit = (row.get("Weight Unit") or "kg").strip().lower()
                if weight_unit == "lbs":
                    weight = _lbs_to_kg(weight)

                # Same ceilings as SetIn. Reps of 0 stay allowed: Strong exports
                # timed holds that way and they have always imported.
                if not (0 <= weight <= MAX_WEIGHT_KG and 0 <= reps <= MAX_REPS):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Weight must be 0-{MAX_WEIGHT_KG:g} kg and reps 0-{MAX_REPS} "
                            f"in row: {dict(row)}"
                        ),
                    )

                workout_date, workout_name = _workout_key(row, fmt)
                row_key = f"{workout_date}:{workout_name}"

                if row_key != current_workout_key:
                    # New workout group — creation is deferred until we know at
                    # least one non-duplicate set belongs to it, so a fully
                    # duplicate re-import doesn't leave behind an empty workout.
                    current_workout_key = row_key
                    current_workout_id = None
                    current_workout_date = workout_date
                    current_workout_name = workout_name

                exercise_id = await get_or_create_exercise(conn, exercise_name)

                dedup_key = (workout_date, exercise_id, weight, reps)
                if existing_counts.get(dedup_key, 0) > 0:
                    existing_counts[dedup_key] -= 1
                    skipped += 1
                    continue

                if current_workout_id is None:
                    started_at = current_workout_date or datetime.now().isoformat()
                    async with conn.execute(
                        "INSERT INTO workouts(started_at, ended_at, notes, user_id) "
                        "VALUES (?, ?, ?, ?)",
                        (started_at, started_at, f"Imported: {current_workout_name}", uid),
                    ) as cur:
                        current_workout_id = cur.lastrowid

                set_notes = (row.get("Notes") or "").strip() or None

                await conn.execute(
                    "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (current_workout_id, exercise_id, reps, weight, set_notes, uid),
                )
                imported += 1
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CSV import failed: %s", exc)
        raise HTTPException(status_code=500, detail="Import failed — transaction rolled back")
    invalidate_exercise_caches()   # the import may have created exercises the coach hasn't seen

    return {"imported": imported, "skipped": skipped}
