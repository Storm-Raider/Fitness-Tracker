"""A bodyweight exercise's record is its best set (added load, then reps), never
the lifter's bodyweight: "Pull-up 80 kg" was bodyweight posing as a lift."""
import pytest


async def _pullup_id(client):
    data = (await client.get("/api/exercises")).json()
    ex = next(e for e in data["exercises"] if e["name"] == "Pull-up")
    assert ex["is_bodyweight"]
    return ex["id"]


async def _bw_set(client, w, ex_id, bodyweight, added, reps):
    return await client.post(f"/workouts/{w}/sets", json={
        "exercise_id": ex_id, "reps": reps, "weight_kg": bodyweight + added, "added_weight_kg": added})


@pytest.mark.asyncio
async def test_a_heavier_bodyweight_is_not_a_record_but_more_reps_is(client):
    ex = await _pullup_id(client)
    w = (await client.post("/workouts", json={"notes": None})).json()["id"]
    assert (await _bw_set(client, w, ex, 80, 0, 10)).json()["is_pr"] is True    # first ever
    assert (await _bw_set(client, w, ex, 82, 0, 8)).json()["is_pr"] is False    # heavier me, fewer reps
    assert (await _bw_set(client, w, ex, 80, 0, 11)).json()["is_pr"] is True    # more reps
    assert (await _bw_set(client, w, ex, 80, 5, 4)).json()["is_pr"] is True     # added load beats reps


@pytest.mark.asyncio
async def test_bodyweight_record_shows_the_best_set_everywhere(client):
    ex = await _pullup_id(client)
    w = (await client.post("/workouts", json={"notes": None})).json()["id"]
    await _bw_set(client, w, ex, 80, 0, 12)
    await _bw_set(client, w, ex, 81, 0, 9)
    await client.post(f"/workouts/{w}/finish")

    home = (await client.get("/", headers={"Accept": "text/html"})).text
    assert "BW × 12" in home and 'data-kg="81.0">81.0 kg' not in home

    analytics = (await client.get("/analytics", headers={"Accept": "text/html"})).text
    assert "BW × 12" in analytics
    assert "e1rm: None" not in analytics          # bodyweight rows stay out of strength levels

    detail = (await client.get(f"/exercises/{ex}", headers={"Accept": "text/html"})).text
    assert "Best: BW × 12" in detail
    assert "Set a weight goal" not in detail and "Rep progression" in detail
    assert 'let metric   = "reps";' in detail      # chart opens on reps, quotes unescaped

    listing = (await client.get("/exercises", headers={"Accept": "text/html"})).text
    assert "Best BW × 12" in listing


@pytest.mark.asyncio
async def test_added_load_outranks_reps_in_the_record(client):
    ex = await _pullup_id(client)
    w = (await client.post("/workouts", json={"notes": None})).json()["id"]
    await _bw_set(client, w, ex, 80, 0, 15)
    await _bw_set(client, w, ex, 80, 10, 5)
    await client.post(f"/workouts/{w}/finish")
    detail = (await client.get(f"/exercises/{ex}", headers={"Accept": "text/html"})).text
    assert 'Best: BW +<span class="weight-display" data-kg="10.0">10.0 kg</span> × 5' in detail
