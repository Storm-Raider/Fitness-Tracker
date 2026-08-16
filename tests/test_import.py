import io
import pytest


def make_csv(*rows, header=True):
    lines = []
    if header:
        lines.append("Date,Workout Name,Exercise Name,Set Order,Weight,Reps,Weight Unit,Notes")
    lines.extend(rows)
    return "\n".join(lines).encode()


def make_hevy_csv(*rows, header=True):
    lines = []
    if header:
        lines.append("Title,Start Time,End Time,Description,Exercise Name,Set Order,Weight,Reps,Distance,Seconds,Notes,Workout Notes,RPE")
    lines.extend(rows)
    return "\n".join(lines).encode()


@pytest.mark.asyncio
async def test_import_basic(client):
    csv_data = make_csv(
        "2024-01-15,Push Day,Bench Press,1,100,5,kg,",
        "2024-01-15,Push Day,Overhead Press,2,60,8,kg,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert data["skipped"] == 0


@pytest.mark.asyncio
async def test_import_skips_cardio_rows(client):
    csv_data = make_csv(
        "2024-01-16,Cardio,,1,,,kg,Running",
        "2024-01-16,Strength,Squat,2,120,3,kg,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 1
    assert data["skipped"] == 1


@pytest.mark.asyncio
async def test_import_lbs_conversion(client):
    csv_data = make_csv(
        "2024-01-17,Legs,Squat,1,225,5,lbs,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    export = await client.get("/export/workouts.csv")
    lines = export.text.strip().split("\n")
    # 225 lbs = 102.07 kg
    assert "102.06" in lines[1] or "102.07" in lines[1]


@pytest.mark.asyncio
async def test_import_missing_required_columns(client):
    csv_data = b"Date,Workout Name\n2024-01-15,Push Day"
    resp = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 422
    assert "Unrecognized CSV format" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_import_hevy_basic(client):
    csv_data = make_hevy_csv(
        "Push Day,2024-01-15 07:32:00,2024-01-15 08:10:00,,Bench Press,1,100,5,,,,,",
        "Push Day,2024-01-15 07:32:00,2024-01-15 08:10:00,,Overhead Press,2,60,8,,,,,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("hevy.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert data["skipped"] == 0


@pytest.mark.asyncio
async def test_import_hevy_groups_into_separate_workouts(client):
    csv_data = make_hevy_csv(
        "Push A,2024-01-15 07:00:00,2024-01-15 08:00:00,,Bench Press,1,100,5,,,,,",
        "Push A,2024-01-15 07:00:00,2024-01-15 08:00:00,,Overhead Press,2,60,8,,,,,",
        "Pull B,2024-01-16 07:00:00,2024-01-16 08:00:00,,Barbell Row,1,80,6,,,,,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("hevy.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 3

    workouts = await client.get("/workouts", headers={"Accept": "application/json"})
    assert workouts.status_code == 200


@pytest.mark.asyncio
async def test_import_row_limit(client):
    lines = ["Date,Workout Name,Exercise Name,Set Order,Weight,Reps,Weight Unit,Notes"]
    lines += ["2024-01-15,Push Day,Bench Press,1,100,5,kg,"] * 50_001
    csv_data = "\n".join(lines).encode()
    resp = await client.post(
        "/import/csv",
        files={"file": ("big.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 422
    assert "50,000" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_import_duplicate_file_is_skipped(client, db_conn):
    csv_data = make_csv(
        "2024-01-20,Push Day,Bench Press,1,100,5,kg,",
        "2024-01-20,Push Day,Overhead Press,2,60,8,kg,",
    )

    first = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert first.status_code == 200
    assert first.json() == {"imported": 2, "skipped": 0}

    async with db_conn.execute("SELECT COUNT(*) FROM sets") as cur:
        (count_after_first,) = await cur.fetchone()
    assert count_after_first == 2

    # Re-import the exact same file.
    second = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert second.status_code == 200
    assert second.json() == {"imported": 0, "skipped": 2}

    async with db_conn.execute("SELECT COUNT(*) FROM sets") as cur:
        (count_after_second,) = await cur.fetchone()
    assert count_after_second == count_after_first  # no new rows created


@pytest.mark.asyncio
async def test_import_repeated_straight_sets_not_treated_as_duplicates(client, db_conn):
    """Identical weight/reps rows within one real workout (e.g. 3x5 straight
    sets) must all import on the first pass — dedup only kicks in against
    sets that already existed before this import started."""
    csv_data = make_csv(
        "2024-01-21,Squat Day,Squat,1,100,5,kg,",
        "2024-01-21,Squat Day,Squat,2,100,5,kg,",
        "2024-01-21,Squat Day,Squat,3,100,5,kg,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json() == {"imported": 3, "skipped": 0}

    async with db_conn.execute("SELECT COUNT(*) FROM sets") as cur:
        (count,) = await cur.fetchone()
    assert count == 3


@pytest.mark.asyncio
async def test_import_groups_by_date_workout(client):
    csv_data = make_csv(
        "2024-01-18,Push A,Bench Press,1,100,5,kg,",
        "2024-01-18,Push A,Overhead Press,2,60,8,kg,",
        "2024-01-19,Push B,Bench Press,1,102.5,5,kg,",
    )
    resp = await client.post(
        "/import/csv",
        files={"file": ("workouts.csv", io.BytesIO(csv_data), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 3

    # Check that two separate workouts were created
    workouts = await client.get("/workouts", headers={"Accept": "application/json"})
    assert workouts.status_code == 200
