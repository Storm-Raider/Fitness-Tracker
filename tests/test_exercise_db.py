"""The verified exercise database (app/data/exercise_db.json, built from
free-exercise-db by scripts/exercise_db/build.py) and how init_db loads it."""
import json
from pathlib import Path

import pytest

from app.db import init_db, _seed_exercise_db
from scripts.exercise_db.curated_map import CURATED_TO_FEDB, EXTRA_COMMON, SHARED_OK
from scripts.exercise_db.zenkai_steps import ZENKAI_STEPS

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
async def test_detail_page_labels_hand_written_steps(client):
    items = (await client.get("/api/exercises")).json()["exercises"]
    burpee = next(e for e in items if e["name"] == "Burpee")
    html = (await client.get(f"/exercises/{burpee['id']}")).text
    assert "How to do it" in html and "Steps written for Zenkai" in html
    assert "no photos for this one" in html
