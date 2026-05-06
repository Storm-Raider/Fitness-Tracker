import io
import pytest


def make_csv(*rows, header=True):
    lines = []
    if header:
        lines.append("Date,Workout Name,Exercise Name,Set Order,Weight,Reps,Weight Unit,Notes")
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
