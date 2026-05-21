import pytest


@pytest.mark.asyncio
async def test_export_csv_empty(client):
    resp = await client.get("/export/workouts.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    lines = resp.text.strip().split("\n")
    assert lines[0] == "date,workout_id,exercise_name,reps,weight_kg,rpe,notes"
    assert len(lines) == 1  # header only


@pytest.mark.asyncio
async def test_export_csv_with_data(client):
    ex = await client.post("/exercises", json={"name": "Squat"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})

    resp = await client.get("/export/workouts.csv")
    assert resp.status_code == 200
    lines = resp.text.strip().split("\n")
    assert len(lines) == 2
    assert "Squat" in lines[1]
    assert "100.0" in lines[1]


@pytest.mark.asyncio
async def test_export_csv_sanitizes_formula_injection(client):
    ex = await client.post("/exercises", json={"name": "FormulaInjectionEx"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]
    await client.post(
        f"/workouts/{w_id}/sets",
        json={"exercise_id": ex_id, "reps": 5, "weight_kg": 80.0,
              "notes": "=SUM(1+1)"},
    )

    resp = await client.get("/export/workouts.csv")
    assert resp.status_code == 200
    assert "'=SUM(1+1)" in resp.text
    assert "=SUM(1+1)" not in resp.text.replace("'=SUM(1+1)", "")
