import json
from datetime import date, timedelta

import pytest


async def _start(client, key="75_hard"):
    r = await client.post("/challenges", json={"template_key": key})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_challenges_page_lists_presets(client):
    r = await client.get("/challenges", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "75 Hard" in r.text and "75 Medium" in r.text


@pytest.mark.asyncio
async def test_start_and_detail(client):
    aid = await _start(client)
    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "75 Hard" in r.text
    assert "Day" in r.text and "of 75" in r.text


@pytest.mark.asyncio
async def test_start_unknown_template_404(client):
    r = await client.post("/challenges", json={"template_key": "nope"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_start_with_past_start_date(client, db):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = await client.post("/challenges", json={"template_key": "75_hard", "start_date": yesterday})
    assert r.status_code == 201
    async with db.execute("SELECT started_on FROM challenge_attempts WHERE id=?", (r.json()["id"],)) as c:
        assert (await c.fetchone())["started_on"] == yesterday


@pytest.mark.asyncio
async def test_start_with_future_date_rejected(client):
    future = (date.today() + timedelta(days=1)).isoformat()
    r = await client.post("/challenges", json={"template_key": "75_hard", "start_date": future})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_checkin_toggles_and_persists(client, db):
    aid = await _start(client)
    today = date.today().isoformat()
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": today, "rule_key": "diet", "done": True})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "active" and d["day_done"] >= 1
    async with db.execute(
        "SELECT rules_json FROM challenge_checkins WHERE attempt_id=? AND day_date=?", (aid, today)
    ) as c:
        assert json.loads((await c.fetchone())["rules_json"])["diet"] is True


@pytest.mark.asyncio
async def test_workout_rule_auto_ticks_from_log(client, db):
    aid = await _start(client)
    today = date.today()
    await db.execute(
        "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
        (today.isoformat() + " 08:00:00", today.isoformat() + " 08:50:00"),
    )
    await db.commit()
    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert "workout logged" in r.text  # workout rule renders its auto-satisfied hint


@pytest.mark.asyncio
async def test_short_workout_does_not_auto_tick(client, db):
    aid = await _start(client)
    today = date.today()
    await db.execute(
        "INSERT INTO workouts(user_id, started_at, ended_at) VALUES (1, ?, ?)",
        (today.isoformat() + " 08:00:00", today.isoformat() + " 08:20:00"),
    )
    await db.commit()
    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert "workout logged" not in r.text  # a 20-minute session must not satisfy a 45-min rule


@pytest.mark.asyncio
async def test_in_progress_workout_does_not_auto_tick(client, db):
    aid = await _start(client)
    today = date.today()
    await db.execute(
        "INSERT INTO workouts(user_id, started_at) VALUES (1, ?)",
        (today.isoformat() + " 08:00:00",),
    )
    await db.commit()
    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert "workout logged" not in r.text  # unfinished workout has no known duration yet


@pytest.mark.asyncio
async def test_reset_on_missed_locked_day(client, db):
    aid = await _start(client)
    # Simulate a challenge that has been running for 4 days: move both
    # started_on AND created_at so the lock window sees real missed days.
    past = (date.today() - timedelta(days=4)).isoformat()
    await db.execute(
        "UPDATE challenge_attempts SET started_on=?, created_at=? WHERE id=?",
        (past, past + " 08:00:00", aid),
    )
    await db.commit()
    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert r.status_code == 200
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        assert (await c.fetchone())["status"] == "failed"
    assert "Run reset" in r.text


@pytest.mark.asyncio
async def test_backdated_start_does_not_trigger_reset(client, db):
    # Regression for: starting with a past start_date immediately tripped the
    # reset because pre-registration days had no check-ins. created_at stays
    # today; days before created_at must not count as missed.
    three_days_ago = (date.today() - timedelta(days=3)).isoformat()
    r = await client.post("/challenges", json={"template_key": "75_hard", "start_date": three_days_ago})
    assert r.status_code == 201
    aid = r.json()["id"]
    # Page load triggers evaluate_attempt — must not flip to failed.
    await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        assert (await c.fetchone())["status"] == "active"


@pytest.mark.asyncio
async def test_yesterday_grace_keeps_active(client, db):
    aid = await _start(client)
    # Start yesterday → Day 1 is yesterday (grace), Day 2 today; no check-ins, still active.
    y = (date.today() - timedelta(days=1)).isoformat()
    await db.execute("UPDATE challenge_attempts SET started_on=? WHERE id=?", (y, aid))
    await db.commit()
    await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        assert (await c.fetchone())["status"] == "active"


@pytest.mark.asyncio
async def test_checkin_rejects_locked_day(client, db):
    # Normally-started challenge (created_at == started_on): days ≥ 2 back are locked.
    aid = await _start(client)
    old = (date.today() - timedelta(days=3)).isoformat()
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": old, "rule_key": "diet", "done": True})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_backdated_challenge_allows_all_prior_days(client, db):
    # Back-dated challenge: every day from started_on through today is editable.
    three_days_ago = (date.today() - timedelta(days=3)).isoformat()
    r = await client.post("/challenges", json={"template_key": "75_hard", "start_date": three_days_ago})
    assert r.status_code == 201
    aid = r.json()["id"]
    # Day 1 (3 days ago) must now be editable even though it's past the grace window.
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": three_days_ago, "rule_key": "diet", "done": True})
    assert r.status_code == 200
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_completion_marks_done_and_unlocks_achievement(client, db):
    today = date.today().isoformat()
    async with db.execute(
        "INSERT INTO challenge_attempts(user_id,template_key,title,total_days,status,started_on) "
        "VALUES (1,'75_hard','75 Hard',1,'active',?)", (today,)
    ) as c:
        aid = c.lastrowid
    await db.commit()
    for rk in ["workout1", "workout2", "diet", "water", "read", "photo"]:
        await client.post(f"/challenges/{aid}/checkin", json={"day_date": today, "rule_key": rk, "done": True})
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        assert (await c.fetchone())["status"] == "completed"
    # /achievements computes + persists the finisher badge
    await client.get("/achievements", headers={"Accept": "text/html"})
    async with db.execute(
        "SELECT 1 FROM user_achievements WHERE user_id=1 AND achievement_id='ch_75hard'"
    ) as c:
        assert await c.fetchone() is not None


@pytest.mark.asyncio
async def test_checkin_on_inactive_409(client):
    aid = await _start(client)
    await client.post(f"/challenges/{aid}/abandon")
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": date.today().isoformat(), "rule_key": "diet", "done": True})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_isolation_between_users(client, user_b_client):
    aid = await _start(client)
    assert (await user_b_client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})).status_code == 404
    r = await user_b_client.post(f"/challenges/{aid}/checkin",
                                 json={"day_date": date.today().isoformat(), "rule_key": "diet", "done": True})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_abandon_and_restart(client, db):
    aid = await _start(client)
    assert (await client.post(f"/challenges/{aid}/abandon")).status_code == 200
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        assert (await c.fetchone())["status"] == "abandoned"
    r = await client.post(f"/challenges/{aid}/restart")
    assert r.status_code == 201 and r.json()["id"] != aid


@pytest.mark.asyncio
async def test_medium_photo_optional_does_not_block_completion(client, db):
    # 75 Medium's photo is optional → a 1-day attempt completes without it.
    today = date.today().isoformat()
    async with db.execute(
        "INSERT INTO challenge_attempts(user_id,template_key,title,total_days,status,started_on) "
        "VALUES (1,'75_medium','75 Medium',1,'active',?)", (today,)
    ) as c:
        aid = c.lastrowid
    await db.commit()
    for rk in ["workout1", "diet", "water", "read"]:  # photo intentionally skipped
        await client.post(f"/challenges/{aid}/checkin", json={"day_date": today, "rule_key": rk, "done": True})
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        assert (await c.fetchone())["status"] == "completed"


async def _seed_daily_checkins(db, aid: int, start: date, today: date, rule_keys: list[str], skip_day_n: int):
    """Insert full check-ins for every day from `start` through `today`, except
    day number `skip_day_n` (1-indexed), which is left with zero check-ins."""
    d = start
    while d <= today:
        day_n = (d - start).days + 1
        if day_n != skip_day_n:
            rules = {rk: True for rk in rule_keys}
            await db.execute(
                "INSERT INTO challenge_checkins(attempt_id, user_id, day_date, rules_json, updated_at) "
                "VALUES (?, 1, ?, ?, datetime('now','localtime'))",
                (aid, d.isoformat(), json.dumps(rules)),
            )
        d += timedelta(days=1)
    await db.commit()


@pytest.mark.asyncio
async def test_fully_missed_day_before_last_day_fails_on_final_day(client, db):
    # Regression: day 74 of a 75-day attempt gets zero check-ins. Days 1-73
    # and day 75 (today, the last day) are fully complete. Evaluating on day
    # 75 must fail the attempt on day 74 — it must NOT read as "completed".
    # (Day 74 used to fall between the fail-check loop's lock window, which
    # stopped at today-2, and the completion check, which only verified the
    # very last day — letting a wholly-skipped day slip through unverified.)
    start = date.today() - timedelta(days=74)
    today = date.today()
    async with db.execute(
        "INSERT INTO challenge_attempts(user_id,template_key,title,total_days,status,started_on,created_at) "
        "VALUES (1,'75_hard','75 Hard',75,'active',?,?)",
        (start.isoformat(), start.isoformat() + " 08:00:00"),
    ) as c:
        aid = c.lastrowid
    await db.commit()

    rule_keys = ["workout1", "workout2", "diet", "water", "read", "photo"]
    await _seed_daily_checkins(db, aid, start, today, rule_keys, skip_day_n=74)

    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert r.status_code == 200
    async with db.execute("SELECT status, ended_on FROM challenge_attempts WHERE id=?", (aid,)) as c:
        row = await c.fetchone()
    assert row["status"] == "failed"
    day_74 = start + timedelta(days=73)  # day_n=74 is the 74th day, i.e. start+73
    assert row["ended_on"] == day_74.isoformat()


@pytest.mark.asyncio
async def test_medium_fully_missed_day_before_last_day_does_not_complete(client, db):
    # Same boundary gap, but for 75 Medium (allow_partial + no_fail). A day
    # with neither full completion nor an explicit partial-submit must still
    # block the run from being marked "completed" on the final day — even
    # though a no_fail template can never flip to "failed".
    start = date.today() - timedelta(days=74)
    today = date.today()
    async with db.execute(
        "INSERT INTO challenge_attempts(user_id,template_key,title,total_days,status,started_on,created_at) "
        "VALUES (1,'75_medium','75 Medium',75,'active',?,?)",
        (start.isoformat(), start.isoformat() + " 08:00:00"),
    ) as c:
        aid = c.lastrowid
    await db.commit()

    rule_keys = ["workout1", "diet", "water", "read"]  # photo is optional, intentionally skipped every day
    await _seed_daily_checkins(db, aid, start, today, rule_keys, skip_day_n=74)

    r = await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})
    assert r.status_code == 200
    async with db.execute("SELECT status FROM challenge_attempts WHERE id=?", (aid,)) as c:
        status = (await c.fetchone())["status"]
    assert status != "completed"


# ── Custom rules (editable challenges) ──────────────────────────────────────

CUSTOM_RULES = [
    {"key": "workout1", "kind": "workout", "label": "Morning run — 30 min"},
    {"key": "journal",  "kind": "manual",  "label": "Write in journal"},
    {"key": "sleep",    "kind": "manual",  "label": "8 hours sleep"},
]


@pytest.mark.asyncio
async def test_custom_rules_stored_and_used(client, db):
    r = await client.post("/challenges", json={"template_key": "75_medium", "rules": CUSTOM_RULES})
    assert r.status_code == 201
    aid = r.json()["id"]
    async with db.execute("SELECT rules_json FROM challenge_attempts WHERE id=?", (aid,)) as c:
        stored = json.loads((await c.fetchone())["rules_json"])
    assert [r["key"] for r in stored] == ["workout1", "journal", "sleep"]
    page = (await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})).text
    assert "Morning run" in page
    assert "Write in journal" in page


@pytest.mark.asyncio
async def test_checkin_accepts_custom_rule_key(client):
    r = await client.post("/challenges", json={"template_key": "75_medium", "rules": CUSTOM_RULES})
    aid = r.json()["id"]
    today = date.today().isoformat()
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": today, "rule_key": "journal", "done": True})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_checkin_rejects_unknown_custom_key(client):
    r = await client.post("/challenges", json={"template_key": "75_medium", "rules": CUSTOM_RULES})
    aid = r.json()["id"]
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": date.today().isoformat(), "rule_key": "diet", "done": True})
    # "diet" is a default key not present in the custom rule set
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_custom_rules_carried_on_restart(client, db):
    r = await client.post("/challenges", json={"template_key": "75_medium", "rules": CUSTOM_RULES})
    aid = r.json()["id"]
    await client.post(f"/challenges/{aid}/abandon")
    r2 = await client.post(f"/challenges/{aid}/restart")
    new_id = r2.json()["id"]
    async with db.execute("SELECT rules_json FROM challenge_attempts WHERE id=?", (new_id,)) as c:
        stored = json.loads((await c.fetchone())["rules_json"])
    assert [r["key"] for r in stored] == ["workout1", "journal", "sleep"]


@pytest.mark.asyncio
async def test_custom_rules_not_allowed_on_non_editable(client):
    r = await client.post("/challenges", json={"template_key": "75_hard", "rules": CUSTOM_RULES})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_custom_rules_empty_list_rejected(client):
    r = await client.post("/challenges", json={"template_key": "75_medium", "rules": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_default_rules_still_work_for_medium(client):
    aid = await _start(client, "75_medium")
    today = date.today().isoformat()
    r = await client.post(f"/challenges/{aid}/checkin",
                          json={"day_date": today, "rule_key": "diet", "done": True})
    assert r.status_code == 200


# ── Edit rules on active challenge ──────────────────────────────────────────

UPDATED_RULES = [
    {"key": "workout1", "kind": "workout", "label": "Evening lift — 45 min"},
    {"key": "journal",  "kind": "manual",  "label": "Evening journal"},
]


@pytest.mark.asyncio
async def test_update_rules_on_active_challenge(client, db):
    aid = await _start(client, "75_medium")
    r = await client.post(f"/challenges/{aid}/rules", json={"rules": UPDATED_RULES})
    assert r.status_code == 200
    async with db.execute("SELECT rules_json FROM challenge_attempts WHERE id=?", (aid,)) as c:
        stored = json.loads((await c.fetchone())["rules_json"])
    assert [r["key"] for r in stored] == ["workout1", "journal"]
    page = (await client.get(f"/challenges/{aid}", headers={"Accept": "text/html"})).text
    assert "Evening lift" in page and "Evening journal" in page


@pytest.mark.asyncio
async def test_update_rules_rejects_inactive(client):
    aid = await _start(client, "75_medium")
    await client.post(f"/challenges/{aid}/abandon")
    r = await client.post(f"/challenges/{aid}/rules", json={"rules": UPDATED_RULES})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_update_rules_rejects_non_editable(client):
    aid = await _start(client, "75_hard")
    r = await client.post(f"/challenges/{aid}/rules", json={"rules": UPDATED_RULES})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_rules_checkin_uses_new_keys(client):
    aid = await _start(client, "75_medium")
    await client.post(f"/challenges/{aid}/rules", json={"rules": UPDATED_RULES})
    today = date.today().isoformat()
    # new key works
    assert (await client.post(f"/challenges/{aid}/checkin",
                               json={"day_date": today, "rule_key": "journal", "done": True})).status_code == 200
    # old default key rejected
    assert (await client.post(f"/challenges/{aid}/checkin",
                               json={"day_date": today, "rule_key": "diet", "done": True})).status_code == 422
