import pytest


@pytest.mark.asyncio
async def test_planner_page_redirects_to_plan(client):
    """GET /planner redirects to /plan with 301."""
    resp = await client.get("/planner", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/plan"


@pytest.mark.asyncio
async def test_plan_page_renders(client, monkeypatch):
    """GET /plan returns 200 with 'Training plan' in the HTML."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    resp = await client.get("/plan", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Training plan" in resp.text


@pytest.mark.asyncio
async def test_plan_page_requires_auth(anon_client):
    """Unauthenticated GET /plan redirects to login."""
    resp = await anon_client.get("/plan", follow_redirects=False)
    assert resp.status_code in (302, 401)


@pytest.mark.asyncio
async def test_save_mesocycle_plan(client, db):
    """POST /planner/plans with valid data creates a plan."""
    payload = {
        "name": "My Test Meso",
        "goal": "strength",
        "weeks": 8,
        "lifts": [{"name": "Back Squat", "e1rm_kg": 100.0}],
    }
    resp = await client.post("/planner/plans", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data

    async with db.execute(
        "SELECT name, goal, weeks FROM mesocycle_plans WHERE id=?", (data["id"],)
    ) as cur:
        row = await cur.fetchone()
    assert row["name"] == "My Test Meso"
    assert row["goal"] == "strength"
    assert row["weeks"] == 8


@pytest.mark.asyncio
async def test_save_mesocycle_invalid_goal(client):
    """POST /planner/plans with an invalid goal returns 422."""
    payload = {
        "name": "Bad Goal",
        "goal": "general",  # not a valid mesocycle goal
        "weeks": 8,
        "lifts": [{"name": "Squat", "e1rm_kg": 100.0}],
    }
    resp = await client.post("/planner/plans", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_mesocycle_plan(client, db):
    """DELETE /planner/plans/{id} removes the plan; second delete returns 404."""
    payload = {"name": "Deletable", "goal": "hypertrophy", "weeks": 4, "lifts": []}
    plan_id = (await client.post("/planner/plans", json=payload)).json()["id"]

    resp = await client.delete(f"/planner/plans/{plan_id}")
    assert resp.status_code == 204

    resp = await client.delete(f"/planner/plans/{plan_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_plan_isolation_between_users(client, user_b_client, db):
    """User B cannot delete User A's mesocycle plan."""
    payload = {"name": "Private", "goal": "peaking", "weeks": 6, "lifts": []}
    plan_id = (await client.post("/planner/plans", json=payload)).json()["id"]

    resp = await user_b_client.delete(f"/planner/plans/{plan_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workout_preloads_exercises_from_routine(client, db):
    """GET /workouts/{id}?routine={rid} passes the routine's exercises as template chips."""
    # Create a routine with one exercise
    async with db.execute("SELECT id FROM exercises LIMIT 1") as cur:
        ex = await cur.fetchone()
    ex_id = ex["id"]
    async with db.execute(
        "INSERT INTO routines(name, user_id) VALUES (?, ?)", ("Test Routine", 1)
    ) as cur:
        rid = cur.lastrowid
    await db.execute(
        "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,?)",
        (rid, ex_id, 0),
    )
    await db.commit()

    # Create a workout
    w = await client.post("/workouts", json={})
    wid = w.json()["id"]

    resp = await client.get(f"/workouts/{wid}?routine={rid}", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    # The exercise name should appear in the template_exercises chips area
    async with db.execute("SELECT name FROM exercises WHERE id=?", (ex_id,)) as cur:
        ex_name = (await cur.fetchone())["name"]
    assert ex_name in resp.text


@pytest.mark.asyncio
async def test_template_chip_onclick_survives_apostrophe_in_name(client, db):
    """Regression test: a routine/template exercise whose name has an apostrophe
    still reaches the session page intact. The logger gets the plan as JSON
    (`template_exercises | tojson` inside a <script>), which renders the chips
    in JS; Jinja's htmlsafe JSON escapes the quote to \\u0027, so the literal
    can't end early and the name round-trips exactly.
    """
    import json
    import re

    ex = await client.post("/exercises", json={"name": "Farmer's Walk"})
    assert ex.status_code == 201
    ex_id = ex.json()["id"]

    async with db.execute(
        "INSERT INTO routines(name, user_id) VALUES (?, ?)", ("Apostrophe Routine", 1)
    ) as cur:
        rid = cur.lastrowid
    await db.execute(
        "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,?)",
        (rid, ex_id, 0),
    )
    await db.commit()

    w = await client.post("/workouts", json={})
    wid = w.json()["id"]

    resp = await client.get(f"/workouts/{wid}?routine={rid}", headers={"Accept": "text/html"})
    assert resp.status_code == 200

    m = re.search(r"let _planned = (\[.*?\]);", resp.text)
    assert m, "the session page doesn't carry the routine's exercises"
    literal = m.group(1)
    assert "'" not in literal          # the apostrophe is escaped, not raw
    assert "\\u0027" in literal
    planned = json.loads(literal)
    assert {"id": ex_id, "name": "Farmer's Walk"} in planned


@pytest.mark.asyncio
async def test_workout_routine_param_ignored_for_finished_workout(client, db):
    """?routine= param has no effect on a finished workout."""
    async with db.execute("SELECT id FROM exercises LIMIT 1") as cur:
        ex_id = (await cur.fetchone())["id"]
    async with db.execute(
        "INSERT INTO routines(name, user_id) VALUES (?, ?)", ("Test Routine 2", 1)
    ) as cur:
        rid = cur.lastrowid
    await db.execute(
        "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,?)",
        (rid, ex_id, 0),
    )
    await db.commit()

    w = await client.post("/workouts", json={})
    wid = w.json()["id"]
    await client.post(f"/workouts/{wid}/finish")

    resp = await client.get(f"/workouts/{wid}?routine={rid}", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    # Finished workouts don't show template chips — the section won't be rendered.
    # The response is a valid 200 page, not an error page.
    assert "Internal Server Error" not in resp.text


@pytest.mark.asyncio
async def test_plan_page_includes_routine_ids_for_ai_plans(client, db, monkeypatch):
    """GET /plan returns HTML with routine_ids data for saved AI plans that have routines."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # Create routines first so we can embed their IDs in plan_json
    async with db.execute(
        "INSERT INTO routines(name, user_id) VALUES (?, ?)", ("Test Plan · Day 1: Push", 1)
    ) as cur:
        rid1 = cur.lastrowid
    async with db.execute(
        "INSERT INTO routines(name, user_id) VALUES (?, ?)", ("Test Plan · Day 2: Pull", 1)
    ) as cur:
        rid2 = cur.lastrowid
    import json as _json
    plan_json = _json.dumps({"days": [], "routine_ids": [rid1, rid2]})
    # Create a coach plan with routine_ids stored in plan_json
    async with db.execute(
        "INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, model) VALUES (?,?,?,?,?,?)",
        (1, "Test Plan", "strength", 2, plan_json, "test"),
    ) as cur:
        plan_id = cur.lastrowid
    await db.commit()

    resp = await client.get("/plan", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    # The plan day chips should appear (routine IDs rendered in the template)
    assert "Day 1" in resp.text
    assert "startPlanDay" in resp.text


@pytest.mark.asyncio
async def test_plan_page_shows_gemini_setup_notice_without_key(client, admin_client, monkeypatch):
    """Everyone is told the AI coach is off; only the admin, who runs the
    server, sees the setup step (an env var means nothing to anyone else)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resp = await client.get("/plan", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "The AI coach is off on this server." in resp.text
    assert "GEMINI_API_KEY" not in resp.text
    assert "Ollama" not in resp.text
    admin = await admin_client.get("/plan", headers={"Accept": "text/html"})
    assert "GEMINI_API_KEY" in admin.text


@pytest.mark.asyncio
async def test_plan_page_hides_setup_notice_with_key(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    resp = await client.get("/plan", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "The AI coach is off" not in resp.text
    assert "test-key" not in resp.text  # the key must never reach the page
