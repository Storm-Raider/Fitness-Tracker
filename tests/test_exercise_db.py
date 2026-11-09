"""The verified exercise database (app/data/exercise_db.json, built from
free-exercise-db by scripts/exercise_db/build.py) and how init_db loads it."""
import json
from pathlib import Path

import pytest

from app.db import init_db, _seed_exercise_db
from scripts.exercise_db.curated_map import CURATED_TO_FEDB, EXTRA_COMMON, SHARED_OK
from scripts.exercise_db.zenkai_steps import ZENKAI_STEPS
from scripts.exercise_db.repdb_map import REPDB_IMAGES

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "app/data/exercise_db.json").read_text())
EXS = DATA["exercises"]

MUSCLES = {"Chest", "Back", "Legs", "Shoulders", "Biceps", "Triceps", "Abs", "Forearms"}
EQUIPMENT = {"Barbell", "Dumbbell", "Cable", "Machine", "Bodyweight", "Kettlebell",
             "EZ Bar", "Band", "Cardio Machine", "Medicine Ball", "Exercise Ball",
             "Foam Roller", "Other"}


# ── dataset ──────────────────────────────────────────────────────────────

def test_count_matches_and_names_unique():
    assert DATA["count"] == len(EXS)
    names = [e["name"].strip().lower() for e in EXS]
    assert len(names) == len(set(names))


def test_every_curated_exercise_is_present_and_common():
    curated = {e["name"]: e for e in EXS if e["source"] == "curated"}
    assert set(curated) == set(CURATED_TO_FEDB)
    assert all(e["tier"] == 1 for e in curated.values())


def test_extra_staples_are_common():
    common_fedb = {e["fedb_id"] for e in EXS if e["source"] == "fedb" and e["tier"] == 1}
    assert len(common_fedb) == len(EXTRA_COMMON)


def test_no_two_curated_lifts_share_a_match_unless_allowed():
    by_match: dict[str, set[str]] = {}
    for name, fedb in CURATED_TO_FEDB.items():
        if fedb:
            by_match.setdefault(fedb, set()).add(name)
    shared = [names for names in by_match.values() if len(names) > 1]
    assert all(names in [set(p) for p in SHARED_OK] for names in shared), shared


def test_database_entries_have_valid_metadata():
    for e in (x for x in EXS if x["source"] == "fedb"):
        assert e["muscle_primary"], e["name"]
        assert set(e["muscle_primary"]) | set(e["muscle_secondary"]) <= MUSCLES, e["name"]
        assert e["equipment"] in EQUIPMENT, (e["name"], e["equipment"])
        assert e["log_type"] in {"reps", "time"}


def test_photos_exist_and_cover_nearly_everything():
    for e in EXS:
        for src in e["images"]:
            assert src.startswith("/static/exercises/")
            assert (ROOT / "app" / src.lstrip("/")).is_file(), src
    with_photos = sum(1 for e in EXS if e["images"])
    assert with_photos >= len(EXS) - 40          # only lifts with no database match lack them


def test_photo_payload_stays_reasonable_for_the_pi():
    total = sum(p.stat().st_size for p in (ROOT / "app/static/exercises").rglob("*.webp"))
    assert total < 25 * 1024 * 1024


def test_every_exercise_has_steps():
    assert [e["name"] for e in EXS if not e["instructions"]] == []


def test_repdb_illustrations_only_where_mapped_and_credited():
    drawn = {e["name"] for e in EXS if e["images"] and "/repdb/" in e["images"][0]}
    assert drawn == set(REPDB_IMAGES)
    readme = (ROOT / "README.md").read_text()
    assert "Exercise data by [RepDB](https://repdb.co)" in readme      # license term 2


def test_every_curated_lift_has_steps_and_a_source():
    for e in (x for x in EXS if x["source"] == "curated"):
        assert e["instructions"], e["name"]
        assert e["steps_source"] in {"fedb", "zenkai"}, e["name"]
        assert e["level"] in {"beginner", "intermediate", "expert"}, e["name"]


def test_zenkai_steps_only_fill_real_gaps():
    """A hand-written entry that the database now covers is stale: drop it."""
    written = {e["name"] for e in EXS if e.get("steps_source") == "zenkai"}
    assert written == set(ZENKAI_STEPS)


# ── seeding ──────────────────────────────────────────────────────────────

async def _count(db, sql, args=()):
    async with db.execute(sql, args) as cur:
        return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_seed_loads_everything_with_muscles(db):
    assert await _count(db, "SELECT COUNT(*) FROM exercises WHERE source IS NOT NULL") == len(EXS)
    assert await _count(db, """SELECT COUNT(*) FROM exercises e WHERE source IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM exercise_muscles m WHERE m.exercise_id=e.id)""") == 0


@pytest.mark.asyncio
async def test_curated_lift_keeps_its_name_and_gains_instructions(db):
    async with db.execute("SELECT * FROM exercises WHERE name='Bench Press'") as cur:
        row = await cur.fetchone()
    assert row["source"] == "curated" and row["tier"] == 1
    assert json.loads(row["instructions"])
    assert len(json.loads(row["images"])) == 2


@pytest.mark.asyncio
async def test_reseed_is_idempotent(db):
    before = await _count(db, "SELECT COUNT(*) FROM exercises")
    muscles = await _count(db, "SELECT COUNT(*) FROM exercise_muscles")
    await db.execute("DELETE FROM app_meta WHERE key='exercise_db'")   # force a full reload
    await init_db(db)
    assert await _count(db, "SELECT COUNT(*) FROM exercises") == before
    assert await _count(db, "SELECT COUNT(*) FROM exercise_muscles") == muscles


@pytest.mark.asyncio
async def test_unchanged_file_is_skipped(db):
    await db.execute("UPDATE exercises SET level='tampered' WHERE name='Bench Press'")
    await _seed_exercise_db(db)
    assert await _count(db, "SELECT COUNT(*) FROM exercises WHERE level='tampered'") == 1


@pytest.mark.asyncio
async def test_user_created_exercise_is_never_taken_over(db):
    name = next(e["name"] for e in EXS if e["source"] == "fedb")
    await db.execute("DELETE FROM exercise_muscles WHERE exercise_id=(SELECT id FROM exercises WHERE name=?)", (name,))
    await db.execute("UPDATE exercises SET source=NULL, category='Mine', equipment='Mine', instructions=NULL WHERE name=?", (name,))
    await db.execute("DELETE FROM app_meta WHERE key='exercise_db'")
    await _seed_exercise_db(db)
    async with db.execute("SELECT * FROM exercises WHERE name=?", (name,)) as cur:
        row = await cur.fetchone()
    assert (row["source"], row["category"], row["equipment"], row["instructions"]) == (None, "Mine", "Mine", None)


# ── ranking + detail ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_exercises_exposes_tier_and_own_usage(client):
    items = (await client.get("/api/exercises")).json()["exercises"]
    assert len(items) >= len(EXS)
    bench = next(e for e in items if e["name"] == "Bench Press")
    assert bench["tier"] == 1 and bench["uses"] == 0
    assert any(e["tier"] == 2 for e in items)


@pytest.mark.asyncio
async def test_exercises_page_folds_the_long_tail(client):
    html = (await client.get("/exercises")).text
    assert 'class="ex-more"' in html
    assert "more chest exercises" in html


@pytest.mark.asyncio
async def test_detail_page_shows_how_to(client):
    items = (await client.get("/api/exercises")).json()["exercises"]
    bench = next(e for e in items if e["name"] == "Bench Press")
    html = (await client.get(f"/exercises/{bench['id']}")).text
    assert "How to do it" in html
    assert "/static/exercises/" in html and "<ol" in html
    assert "From free-exercise-db" in html


@pytest.mark.asyncio
async def test_detail_page_labels_hand_written_steps_and_credits_illustrations(client):
    items = {e["name"]: e for e in (await client.get("/api/exercises")).json()["exercises"]}
    burpee = (await client.get(f"/exercises/{items['Burpee']['id']}")).text
    assert "Steps written for Zenkai" in burpee
    assert "/static/exercises/repdb/" in burpee and 'href="https://repdb.co"' in burpee
    assert "<figcaption>" not in burpee              # one illustration: no Start/Finish caption
    swim = (await client.get(f"/exercises/{items['Swimming']['id']}")).text
    assert "How to do it" in swim and "no photos for this one" in swim


# ── in-session: picker thumbnails + How-to sheet ─────────────────────────

@pytest.mark.asyncio
async def test_api_exercises_has_thumb_and_howto_flag(client):
    items = {e["name"]: e for e in (await client.get("/api/exercises")).json()["exercises"]}
    assert items["Bench Press"]["thumb"].startswith("/static/exercises/")
    assert items["Bench Press"]["has_howto"] is True
    assert items["Swimming"]["thumb"] is None and items["Swimming"]["has_howto"] is True   # steps, no photo


@pytest.mark.asyncio
async def test_howto_endpoint(client):
    items = {e["name"]: e for e in (await client.get("/api/exercises")).json()["exercises"]}
    h = (await client.get(f"/api/exercises/{items['Burpee']['id']}/howto")).json()
    assert h["name"] == "Burpee" and h["instructions"] and h["illustrated"] is True
    assert h["steps_source"] == "zenkai"
    b = (await client.get(f"/api/exercises/{items['Bench Press']['id']}/howto")).json()
    assert len(b["images"]) == 2 and b["illustrated"] is False
    assert (await client.get("/api/exercises/999999/howto")).status_code == 404


@pytest.mark.asyncio
async def test_howto_endpoint_needs_login(anon_client):
    r = await anon_client.get("/api/exercises/1/howto", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# ── offline pack: what the phone saves ahead of time ─────────────────────

@pytest.mark.asyncio
async def test_offline_pack_puts_the_athletes_own_exercises_first(client, db):
    items = {e["name"]: e for e in (await client.get("/api/exercises")).json()["exercises"]}
    # A routine with a long-tail exercise, and a logged set of another one.
    tail = next(e for e in items.values() if e["tier"] == 2 and e["thumb"])
    r = await client.post("/routines", json={"name": "Mine", "exercise_ids": [tail["id"]]})
    assert r.status_code in (200, 201), r.text
    w = (await client.post("/workouts", json={})).json()["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": items["Pendlay Row"]["id"], "reps": 5, "weight_kg": 60})

    pack = (await client.get("/api/exercises/offline-pack")).json()
    ids = pack["exercises"]
    assert ids[0] == tail["id"]                                  # routines first
    assert ids.index(items["Pendlay Row"]["id"]) < ids.index(items["Bench Press"]["id"])
    assert items["Bench Press"]["id"] in ids                     # then the common tier
    assert len(ids) <= 250
    urls = pack["urls"]
    assert f"/api/exercises/{tail['id']}/howto" in urls and tail["thumb"] in urls
    assert all(u.startswith(("/static/exercises/", "/api/exercises/")) for u in urls)


@pytest.mark.asyncio
async def test_offline_pack_needs_login(anon_client):
    r = await anon_client.get("/api/exercises/offline-pack", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


def test_service_worker_keeps_exercise_media_across_versions():
    sw = (ROOT / "app/static/sw.js").read_text()
    assert "const MEDIA_CACHE = 'zenkai-exercise-media-v1'" in sw
    assert "k !== MEDIA_CACHE" in sw                              # not deleted on activate
    assert "warm-exercise-media" in sw
