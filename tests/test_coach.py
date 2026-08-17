import asyncio
import json

import pytest

from app.routes import coach


async def _generate(client, goal, days, **extra):
    """Start a generation job and poll the status endpoint until it settles."""
    r = await client.post(
        "/coach/generate", json={"goal": goal, "days_per_week": days, **extra}
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    for _ in range(100):
        pr = await client.get(f"/coach/generate/{job_id}")
        assert pr.status_code == 200
        pd = pr.json()
        if pd["status"] in ("done", "error"):  # terminal states
            return pd
        await asyncio.sleep(0.02)
    raise AssertionError("generation job never finished")


async def _real_exercise_names(db, n=3):
    """Pull real seeded exercise names so fake plans resolve to valid ids."""
    async with db.execute(
        "SELECT name FROM exercises WHERE COALESCE(category,'') != 'Cardio' ORDER BY name LIMIT ?",
        (n,),
    ) as cur:
        return [r["name"] for r in await cur.fetchall()]


def _fake_chat(plan: dict):
    async def _inner(system, user, schema, **kwargs):
        return plan
    return _inner


@pytest.fixture(autouse=True)
def _reset_coach_state():
    """Coach module state is process-global — clear it around every test so a
    speculative plan cached by one test can't satisfy the next test's request."""
    coach._JOBS.clear(); coach._QUEUE.clear()
    coach._ACTIVE_BY_USER.clear(); coach._SPEC_CACHE.clear()
    yield
    coach._JOBS.clear(); coach._QUEUE.clear()
    coach._ACTIVE_BY_USER.clear(); coach._SPEC_CACHE.clear()


@pytest.mark.asyncio
async def test_coach_page_redirects_to_plan(client):
    resp = await client.get("/coach", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/plan"


@pytest.mark.asyncio
async def test_coach_page_requires_auth(anon_client):
    # Auth middleware runs before our 301, so unauthenticated requests get a 302
    # to /login. Either redirect status is acceptable — the point is no 200.
    resp = await anon_client.get("/coach", follow_redirects=False)
    assert resp.status_code in (301, 302)


@pytest.mark.asyncio
async def test_generate_returns_plan_and_drops_unknowns(client, db, monkeypatch):
    names = await _real_exercise_names(db, 3)
    fake_plan = {
        "title": "Test Split",
        "summary": "A test routine.",
        "days": [
            {"focus": "Day A", "exercises": [
                {"name": names[0], "sets": 4, "reps": "8-12", "note": "controlled"},
                {"name": "Totally Fake Lift", "sets": 3, "reps": "10"},
            ]},
            {"focus": "Day B", "exercises": [
                {"name": names[1], "sets": 5, "reps": "5"},
                {"name": names[2], "sets": 3, "reps": "12"},
            ]},
        ],
    }
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(fake_plan))

    data = await _generate(client, "strength", 2)
    assert data["status"] == "done"
    assert data["plan"]["goal"] == "strength"
    assert len(data["plan"]["days"]) == 2
    # Unknown exercise filtered out, real ones resolved with ids
    day_a = data["plan"]["days"][0]
    assert [e["name"] for e in day_a["exercises"]] == [names[0]]
    assert day_a["exercises"][0]["exercise_id"] > 0
    assert "Totally Fake Lift" in data["dropped"]


@pytest.mark.asyncio
async def test_generate_caps_days_to_request(client, db, monkeypatch):
    names = await _real_exercise_names(db, 2)
    fake_plan = {
        "title": "Big Plan", "summary": "",
        "days": [
            {"focus": f"D{i}", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}
            for i in range(5)
        ],
    }
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(fake_plan))

    data = await _generate(client, "general", 3)
    assert data["status"] == "done"
    assert len(data["plan"]["days"]) == 3


@pytest.mark.asyncio
async def test_generate_handles_ollama_error(client, monkeypatch):
    async def boom(system, user, schema, **kwargs):
        raise coach.ollama.OllamaError("Couldn't reach Ollama")
    monkeypatch.setattr(coach.ollama, "chat_json", boom)

    data = await _generate(client, "strength", 3)
    assert data["status"] == "error"
    assert "Ollama" in data["error"]


@pytest.mark.asyncio
async def test_generate_empty_plan_errors(client, monkeypatch):
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat({"title": "x", "summary": "", "days": []}))
    data = await _generate(client, "strength", 3)
    assert data["status"] == "error"
    assert "usable exercises" in data["error"]


@pytest.mark.asyncio
async def test_generate_validates_input(client):
    resp = await client.post("/coach/generate", json={"goal": "bogus", "days_per_week": 3})
    assert resp.status_code == 422
    resp = await client.post("/coach/generate", json={"goal": "strength", "days_per_week": 99})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_generation_status_unknown_job_404(client):
    resp = await client.get("/coach/generate/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_single_flight_reuses_inflight_job(client, db, monkeypatch):
    # A second request while one is still running must reuse the same job_id,
    # so repeated clicks / a stale tab can't spawn multiple Ollama generations.
    names = await _real_exercise_names(db, 1)
    gate = asyncio.Event()

    async def slow(system, user, schema, **kwargs):
        await gate.wait()
        return {"title": "P", "summary": "", "days": [
            {"focus": "A", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}]}
    monkeypatch.setattr(coach.ollama, "chat_json", slow)

    r1 = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 1})
    r2 = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 1})
    assert r1.json()["job_id"] == r2.json()["job_id"]

    gate.set()  # let the single job finish
    job_id = r1.json()["job_id"]
    for _ in range(100):
        pd = (await client.get(f"/coach/generate/{job_id}")).json()
        if pd["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.02)
    assert pd["status"] == "done"


@pytest.mark.asyncio
async def test_generation_job_isolated_between_users(client, user_b_client, db, monkeypatch):
    names = await _real_exercise_names(db, 1)
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(
        {"title": "P", "summary": "", "days": [
            {"focus": "A", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}]}
    ))
    r = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 1})
    job_id = r.json()["job_id"]
    # user B cannot read user A's job
    resp = await user_b_client.get(f"/coach/generate/{job_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_save_creates_plan_and_routines(client, db):
    names = await _real_exercise_names(db, 2)
    payload = {
        "title": "My Coached Plan",
        "summary": "summary",
        "goal": "hypertrophy",
        "days_per_week": 2,
        "days": [
            {"focus": "Upper", "exercises": [{"name": names[0], "sets": 4, "reps": "8-12", "note": ""}]},
            {"focus": "Lower", "exercises": [{"name": names[1], "sets": 3, "reps": "10", "note": "deep"}]},
        ],
    }
    resp = await client.post("/coach/save", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["routine_ids"]) == 2

    # coach_plan persisted
    async with db.execute("SELECT title, plan_json FROM coach_plans WHERE id = ?", (data["id"],)) as cur:
        row = await cur.fetchone()
    assert row["title"] == "My Coached Plan"
    plan = json.loads(row["plan_json"])
    assert plan["days"][0]["exercises"][0]["exercise_id"] > 0

    # routines show up in the user's routine list with day labels
    listing = await client.get("/routines")
    routine_names = [r["name"] for r in listing.json()]
    assert any("My Coached Plan · Day 1: Upper" == n for n in routine_names)
    assert any("Day 2: Lower" in n for n in routine_names)


@pytest.mark.asyncio
async def test_save_rejects_all_invalid_exercises(client):
    payload = {
        "title": "Bad Plan", "summary": "", "goal": "strength", "days_per_week": 1,
        "days": [{"focus": "X", "exercises": [{"name": "Nonexistent Move", "sets": 3, "reps": "5"}]}],
    }
    resp = await client.post("/coach/save", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_plan(client, db):
    names = await _real_exercise_names(db, 1)
    payload = {
        "title": "Deletable", "summary": "", "goal": "general", "days_per_week": 1,
        "days": [{"focus": "Full", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}],
    }
    pid = (await client.post("/coach/save", json=payload)).json()["id"]

    resp = await client.delete(f"/coach/plans/{pid}")
    assert resp.status_code == 204

    resp = await client.delete(f"/coach/plans/{pid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_plan_isolation_between_users(client, user_b_client, db):
    names = await _real_exercise_names(db, 1)
    payload = {
        "title": "Private", "summary": "", "goal": "general", "days_per_week": 1,
        "days": [{"focus": "Full", "exercises": [{"name": names[0], "sets": 3, "reps": "10"}]}],
    }
    pid = (await client.post("/coach/save", json=payload)).json()["id"]

    # user B cannot delete user A's plan
    resp = await user_b_client.delete(f"/coach/plans/{pid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_catalog_prioritises_conventional_compounds(db):
    """The exercise catalog must surface staple compounds (Back Squat, Bench
    Press, Deadlift) even for a user with no logged history."""
    catalog = await coach._exercise_catalog(db, uid=1)
    flat = coach._catalog_names(catalog)
    for staple in ("Back Squat", "Bench Press", "Deadlift", "Barbell Row", "Overhead Press"):
        assert staple in flat, f"{staple} missing from coach catalog"


@pytest.mark.asyncio
async def test_prompt_includes_split_and_prescription(db):
    """The generated prompt must carry the split guide + goal prescription so
    the model produces conventional programming."""
    profile = await coach.build_profile(db, uid=1)
    catalog = await coach._exercise_catalog(db, uid=1)
    prompt = coach._build_prompt("strength", 3, profile, catalog, "")
    assert "RECOMMENDED SPLIT" in prompt
    assert "Push / Pull / Legs" in prompt
    assert "PRESCRIPTION" in prompt
    assert "80–90% 1RM" in prompt
    assert "PROGRESSION" in prompt


# ── Prompt actually carries the athlete's comments/RPE/journal signal ─────

def _base_profile(**overrides):
    profile = {
        "total_workouts": 10, "last_day": "2026-08-15", "sessions_per_week": 3,
        "top_exercises": [], "top_lifts": [], "avg_weekly_sets": {}, "undertrained": [],
        "preferred_equipment": [], "muscle_recovery": {}, "stalled": [],
        "bodyweight_kg": None, "avg_session_minutes": None, "exercise_goals": [],
        "last_plan_feedback": None, "recent_set_notes": [], "high_effort_lifts": [],
        "low_effort_lifts": [], "wellness": {}, "injury_flags": [],
    }
    profile.update(overrides)
    return profile


@pytest.mark.asyncio
async def test_prompt_surfaces_injury_flags_as_non_negotiable(db):
    catalog = await coach._exercise_catalog(db, uid=1)
    profile = _base_profile(injury_flags=[
        {"text": "tweaked my knee on the descent", "exercise": "Back Squat", "days_ago": 1},
    ])
    prompt = coach._build_prompt("strength", 3, profile, catalog, "")
    assert "ATHLETE FLAGGED PAIN/DISCOMFORT" in prompt
    assert "tweaked my knee on the descent" in prompt
    assert "NON-NEGOTIABLE" in prompt
    assert "RESPECT FLAGGED PAIN" in prompt


@pytest.mark.asyncio
async def test_prompt_surfaces_rpe_trend_and_wellness(db):
    catalog = await coach._exercise_catalog(db, uid=1)
    profile = _base_profile(
        high_effort_lifts=[{"name": "Back Squat", "avg_rpe": 9.0, "n": 2}],
        low_effort_lifts=[{"name": "Barbell Curl", "avg_rpe": 4.5, "n": 2}],
        wellness={"avg_sleep_hrs": 5.0, "low_energy_days": 3, "low_motivation_days": 2, "recent_notes": []},
    )
    prompt = coach._build_prompt("hypertrophy", 3, profile, catalog, "")
    assert "HIGH EFFORT" in prompt and "Back Squat" in prompt
    assert "LOW EFFORT" in prompt and "Barbell Curl" in prompt
    assert "RECENT WELLNESS" in prompt
    assert "avg sleep 5.0h/night" in prompt
    assert "AUTOREGULATE" in prompt


@pytest.mark.asyncio
async def test_prompt_surfaces_recent_workout_and_journal_comments(db):
    catalog = await coach._exercise_catalog(db, uid=1)
    profile = _base_profile(
        recent_set_notes=[{"name": "Bench Press", "notes": "felt strong today", "days_ago": 2}],
        wellness={"recent_notes": [{"date": "2026-08-15", "note": "slept badly, low energy"}]},
    )
    prompt = coach._build_prompt("general", 3, profile, catalog, "")
    assert "Bench Press: \"felt strong today\"" in prompt
    assert "slept badly, low energy" in prompt


def test_system_prompt_instructs_use_of_athlete_comments():
    assert "ATHLETE'S OWN WORDS" in coach._SYSTEM_PROMPT
    assert "Never program through flagged pain" in coach._SYSTEM_PROMPT


# ── Queue system ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_cap_rejects_when_full(client, monkeypatch):
    """When _MAX_QUEUE jobs are already active, a new request gets 429."""
    # Simulate a full queue: fill _JOBS with active jobs owned by other users.
    monkeypatch.setattr(coach, "_MAX_QUEUE", 3)
    coach._JOBS.clear(); coach._QUEUE.clear()
    for i in range(3):
        jid = f"busy-{i}"
        coach._JOBS[jid] = {"status": "queued", "user_id": 999 - i}
        coach._QUEUE.append(jid)

    r = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 2})
    assert r.status_code == 429
    assert "busy" in r.json()["detail"].lower()
    coach._JOBS.clear(); coach._QUEUE.clear()


@pytest.mark.asyncio
async def test_status_reports_queue_position(client, monkeypatch):
    """A queued job reports its 1-based position and how many are ahead."""
    coach._JOBS.clear(); coach._QUEUE.clear()
    # Two other jobs ahead, then this user's job (id=1).
    for jid, uid in [("a", 50), ("b", 51)]:
        coach._JOBS[jid] = {"status": "queued", "user_id": uid}
        coach._QUEUE.append(jid)
    mine = "mine"
    coach._JOBS[mine] = {"status": "queued", "user_id": 1}
    coach._QUEUE.append(mine)

    pr = await client.get(f"/coach/generate/{mine}")
    assert pr.status_code == 200
    pd = pr.json()
    assert pd["status"] == "queued"
    assert pd["position"] == 3
    assert pd["ahead"] == 2
    coach._JOBS.clear(); coach._QUEUE.clear()


@pytest.mark.asyncio
async def test_processing_status_reported(client):
    coach._JOBS.clear(); coach._QUEUE.clear()
    coach._JOBS["p"] = {"status": "processing", "user_id": 1}
    pr = await client.get("/coach/generate/p")
    assert pr.json()["status"] == "processing"
    coach._JOBS.clear()


@pytest.mark.asyncio
async def test_single_flight_attaches_to_queued_job(client, monkeypatch):
    """A second request from the same user while one is queued reuses the job."""
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear()
    coach._JOBS["existing"] = {"status": "queued", "user_id": 1}
    coach._ACTIVE_BY_USER[1] = "existing"
    coach._QUEUE.append("existing")

    r = await client.post("/coach/generate", json={"goal": "general", "days_per_week": 2})
    assert r.status_code == 202
    assert r.json()["job_id"] == "existing"  # attached, not a new job
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear()


# ── Plan diversity: detection + deterministic repair ─────────────────

def _mk_plan(days_per_week, day_specs):
    """day_specs: list of [(exercise_id, name), ...] per day."""
    return {
        "title": "T", "summary": "", "goal": "general",
        "days_per_week": days_per_week,
        "days": [
            {"focus": f"Day {i+1}", "exercises": [
                {"exercise_id": eid, "name": name, "sets": 3, "reps": "8-12", "note": ""}
                for eid, name in spec
            ]}
            for i, spec in enumerate(day_specs)
        ],
    }


def test_max_weekly_repeats_boundaries():
    assert coach._max_weekly_repeats(1) == 1
    assert coach._max_weekly_repeats(2) == 1
    assert coach._max_weekly_repeats(3) == 2
    assert coach._max_weekly_repeats(5) == 2
    assert coach._max_weekly_repeats(6) == 3
    assert coach._max_weekly_repeats(7) == 3


def test_quality_issues_flags_identical_days():
    plan = _mk_plan(2, [
        [(1, "A"), (2, "B"), (3, "C")],
        [(1, "A"), (2, "B"), (3, "C")],
    ])
    issues = coach._plan_quality_issues(plan)
    assert any("share" in i for i in issues)


def test_quality_issues_flags_over_repeated_exercise():
    # 3-day plan (cap 2): exercise 1 on all 3 days.
    plan = _mk_plan(3, [
        [(1, "Squat"), (2, "B")],
        [(1, "Squat"), (3, "C")],
        [(1, "Squat"), (4, "D")],
    ])
    issues = coach._plan_quality_issues(plan)
    assert any("Squat" in i and "3 days" in i for i in issues)


def test_quality_issues_clean_plan_passes():
    plan = _mk_plan(3, [
        [(1, "A"), (2, "B")],
        [(3, "C"), (4, "D")],
        [(5, "E"), (6, "F")],
    ])
    assert coach._plan_quality_issues(plan) == []


@pytest.mark.asyncio
async def test_repair_dedupes_within_day(db):
    await coach._exercise_catalog(db, 0)          # populate _EXERCISE_BASE_ROWS
    name_map, _ = await coach._name_to_id_map(db)
    sq = name_map["back squat"]
    plan = _mk_plan(1, [[(sq["id"], sq["name"]), (sq["id"], sq["name"])]])
    repaired, _swaps = coach._repair_plan(plan, name_map)
    assert len(repaired["days"][0]["exercises"]) == 1


@pytest.mark.asyncio
async def test_repair_swaps_over_repeated_exercise(db):
    await coach._exercise_catalog(db, 0)
    name_map, _ = await coach._name_to_id_map(db)
    sq = name_map["back squat"]
    # 3-day plan (cap 2): Back Squat on all 3 days → day 3 must get a swap.
    plan = _mk_plan(3, [
        [(sq["id"], sq["name"])],
        [(sq["id"], sq["name"])],
        [(sq["id"], sq["name"])],
    ])
    repaired, swaps = coach._repair_plan(plan, name_map)
    day3 = repaired["days"][2]["exercises"]
    assert day3[0]["exercise_id"] != sq["id"], "third occurrence should be swapped"
    assert swaps and "Back Squat →" in swaps[0]
    # Replacement must be a real library exercise.
    assert day3[0]["name"].lower() in name_map


@pytest.mark.asyncio
async def test_generation_repairs_copy_paste_days(client, db, monkeypatch):
    """A model that returns the same day twice still yields a diverse plan."""
    names = await _real_exercise_names(db, 4)
    same_day = {
        "focus": "Full Body",
        "exercises": [{"name": n, "sets": 3, "reps": "8-12", "note": ""} for n in names],
    }
    fake = {"title": "Copy Paste", "summary": "", "days": [same_day, dict(same_day)]}
    monkeypatch.setattr(coach.ollama, "chat_json", _fake_chat(fake))
    coach._JOBS.clear(); coach._QUEUE.clear(); coach._ACTIVE_BY_USER.clear()

    pd = await _generate(client, "general", 2)
    assert pd["status"] == "done"
    d1 = {e["exercise_id"] for e in pd["plan"]["days"][0]["exercises"]}
    d2 = {e["exercise_id"] for e in pd["plan"]["days"][1]["exercises"]}
    # cap for 2 days/week is 1 — repair must make the days fully disjoint.
    assert d1.isdisjoint(d2), f"days still overlap after repair: {d1 & d2}"


def test_system_prompt_forbids_copying_example_numbers():
    assert "placeholders" in coach._SYSTEM_PROMPT
    assert "Copying this example's names, numbers, or phrasing" in coach._SYSTEM_PROMPT
    # The old example's specific weight/rep combination must not survive —
    # a real generation against live data reused these numbers verbatim.
    assert '"@ 28 kg — increase by 2 kg when hitting 12"' not in coach._SYSTEM_PROMPT


def test_system_prompt_example_uses_unresolvable_exercise_names():
    """The example's exercises must not be real catalog entries — a real
    generation reused the OLD example's real names (Bench Press, Overhead
    Press, ...) verbatim, sets/reps/notes included, for an athlete with weak
    personalization signal for that muscle group. Since exercise `name` is
    enum-constrained to the real per-request catalog, an unresolvable
    placeholder like "Exercise A" can never be copied into real output."""
    for real_name in ("Bench Press", "Overhead Press", "Incline Dumbbell Press",
                       "Lateral Raise", "Tricep Pushdown"):
        assert f'"name": "{real_name}"' not in coach._SYSTEM_PROMPT
    for placeholder in ("Exercise A", "Exercise B", "Exercise C", "Exercise D", "Exercise E"):
        assert placeholder in coach._SYSTEM_PROMPT


# ── Detecting verbatim example-copying (backstop for the fix above) ───

def _plan_with_notes(notes: list[str]) -> dict:
    return {
        "days_per_week": 1,
        "days": [{"focus": "Push", "exercises": [
            {"exercise_id": i, "name": f"Ex{i}", "note": n} for i, n in enumerate(notes)
        ]}],
    }


def test_quality_issues_flags_multiple_verbatim_example_phrases():
    plan = _plan_with_notes(coach._EXAMPLE_NOTE_PHRASES[:3])  # 3 of 5 — at threshold
    issues = coach._plan_quality_issues(plan)
    assert any("worked example wording verbatim" in i for i in issues)


def test_quality_issues_ignores_a_single_generic_phrase():
    # One plausible, generic cue on its own isn't evidence of copying — only
    # several at once is.
    plan = _plan_with_notes([coach._EXAMPLE_NOTE_PHRASES[0], "some other note", "another note"])
    issues = coach._plan_quality_issues(plan)
    assert not any("worked example wording verbatim" in i for i in issues)


# ── Schema tightening (token-budget on a ~5 tok/s Pi) ──────────────────

def test_schema_caps_free_text_fields_to_save_output_tokens():
    schema = coach._plan_schema(3, allowed_names=["Bench Press"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["title"]["maxLength"] == 60
    assert schema["properties"]["summary"]["maxLength"] == 200
    day_schema = schema["properties"]["days"]["items"]
    assert day_schema["additionalProperties"] is False
    assert day_schema["properties"]["focus"]["maxLength"] == 20
    ex_schema = day_schema["properties"]["exercises"]["items"]
    assert ex_schema["additionalProperties"] is False
    assert ex_schema["properties"]["reps"]["maxLength"] == 12
    assert ex_schema["properties"]["note"]["maxLength"] == 90  # unchanged
    assert ex_schema["properties"]["sets"]["minimum"] == 1
    assert ex_schema["properties"]["sets"]["maximum"] == 20


# ── num_ctx sizing accounts for the schema, not just the prompt text ──

def test_size_num_ctx_grows_with_a_larger_schema():
    prompt = "x" * 100
    small_schema = coach._plan_schema(1, allowed_names=["Bench Press"])
    # A big catalog produces a big `enum` list in the schema — that has to be
    # held in context too, even though it's sent as its own `format` field,
    # not appended to the prompt string.
    big_schema = coach._plan_schema(1, allowed_names=[f"Exercise {i}" for i in range(400)])
    assert coach._size_num_ctx(prompt, big_schema) >= coach._size_num_ctx(prompt, small_schema)


def test_size_num_ctx_clamped_to_range():
    tiny_schema = coach._plan_schema(1, allowed_names=["Bench Press"])
    assert coach._size_num_ctx("", tiny_schema) >= 2048
    huge_prompt = "x" * 100_000
    assert coach._size_num_ctx(huge_prompt, tiny_schema) <= 8192


# ── Retry on transport/parse failure (distinct from the quality retry) ─

@pytest.mark.asyncio
async def test_generate_retries_once_on_transport_error_then_succeeds(client, db, monkeypatch):
    names = await _real_exercise_names(db, 1)
    fake_plan = {
        "title": "Recovered Plan", "summary": "",
        "days": [{"focus": "Full Body", "exercises": [
            {"name": names[0], "sets": 3, "reps": "10"},
        ]}],
    }
    calls = {"n": 0}

    async def _flaky(system, user, schema, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise coach.ollama.OllamaError("Ollama returned malformed JSON.")
        return fake_plan

    monkeypatch.setattr(coach.ollama, "chat_json", _flaky)

    data = await _generate(client, "general", 1)
    assert data["status"] == "done"
    assert data["plan"]["title"] == "Recovered Plan"
    # At least 2: the failed first attempt + the retry that recovered. Not
    # asserting an exact count — a successful generation also kicks off a
    # background speculative-cache generation (see _run_spec_generation) that
    # calls chat_json too, and whether it's fired by the time we check here
    # is a scheduling race, not something this test cares about.
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_generate_gives_up_after_retry_still_fails(client, monkeypatch):
    async def _always_fails(system, user, schema, **kwargs):
        raise coach.ollama.OllamaError("Couldn't reach Ollama")

    monkeypatch.setattr(coach.ollama, "chat_json", _always_fails)

    data = await _generate(client, "strength", 3)
    assert data["status"] == "error"
    assert "Ollama" in data["error"]


# ── Spec-cache generation matches the primary path's quality bar ──────

@pytest.mark.asyncio
async def test_spec_generation_uses_same_temperature_as_primary_path(client, db, monkeypatch):
    names = await _real_exercise_names(db, 1)
    fake_plan = {
        "title": "Spec Plan", "summary": "",
        "days": [{"focus": "Full Body", "exercises": [
            {"name": names[0], "sets": 3, "reps": "10"},
        ]}],
    }
    temps_seen = []

    async def _recording_chat(system, user, schema, **kwargs):
        temps_seen.append(kwargs.get("temperature"))
        return fake_plan

    monkeypatch.setattr(coach.ollama, "chat_json", _recording_chat)

    await _generate(client, "general", 1)
    # Primary generation call already fired; now let the background spec-gen
    # task (kicked off after a successful generation) run to completion.
    for _ in range(50):
        if len(temps_seen) >= 2:
            break
        await asyncio.sleep(0.02)

    assert len(temps_seen) >= 2, "speculative generation never ran"
    assert temps_seen[0] == temps_seen[1] == 0.2
