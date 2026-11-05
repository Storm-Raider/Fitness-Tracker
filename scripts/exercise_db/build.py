#!/usr/bin/env python3
"""Build Zenkai's bundled exercise database from free-exercise-db (public domain,
https://github.com/yuhonas/free-exercise-db).

    git clone --depth 1 https://github.com/yuhonas/free-exercise-db /tmp/fedb
    python3 scripts/exercise_db/build.py /tmp/fedb

Writes app/data/exercise_db.json and small WebP photos (two per exercise) in
app/static/exercises/<id>/. Deterministic: the same checkout gives the same
output, so a rebuild shows only real upstream changes in the diff.

Rules:
- The 169 curated exercises keep their names, equipment, muscles and cues (logged
  history is keyed to them). From their matched entry (curated_map.py) they take
  instructions, level and photos.
- Every other entry is added under its own name, mapped into Zenkai's
  vocabulary, unless its name would duplicate a curated one ("Split Squats" next
  to "Split Squat"), in which case it is skipped.
- Tier 1 (Common) = the curated exercises + EXTRA_COMMON. Tier 2 = the rest.
  Every exercise the database has photos for gets two (400px WebP, ~10 KB each);
  tier only decides ordering and folding.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.data.exercises import EXERCISES  # noqa: E402
from scripts.exercise_db.curated_map import CURATED_TO_FEDB, EXTRA_COMMON  # noqa: E402
from scripts.exercise_db.zenkai_steps import ZENKAI_STEPS  # noqa: E402

OUT_JSON = ROOT / "app/data/exercise_db.json"
OUT_IMG = ROOT / "app/static/exercises"
IMG_WIDTH, IMG_QUALITY = 400, 68

MUSCLE = {
    "abdominals": "Abs", "chest": "Chest", "shoulders": "Shoulders", "biceps": "Biceps",
    "triceps": "Triceps", "forearms": "Forearms", "lats": "Back", "middle back": "Back",
    "lower back": "Back", "traps": "Back", "neck": "Back", "quadriceps": "Legs",
    "hamstrings": "Legs", "glutes": "Legs", "calves": "Legs", "adductors": "Legs",
    "abductors": "Legs",
}
EQUIPMENT = {
    "barbell": "Barbell", "dumbbell": "Dumbbell", "cable": "Cable", "machine": "Machine",
    "body only": "Bodyweight", "kettlebells": "Kettlebell", "e-z curl bar": "EZ Bar",
    "bands": "Band", "medicine ball": "Medicine Ball", "exercise ball": "Exercise Ball",
    "foam roll": "Foam Roller", "other": "Other", None: "Other",
}
LEGS = {"quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"}


def norm(name: str) -> str:
    """Compare names loosely: case, punctuation and a trailing plural s."""
    n = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"s\b", "", n)


def category(x: dict) -> str:
    cat = x["category"]
    if cat == "stretching":
        return "Mobility"
    if cat == "cardio":
        return "Cardio"
    if cat == "olympic weightlifting":
        return "Olympic"
    if cat == "strongman":
        return "Full Body"
    prim = set(x.get("primaryMuscles") or [])
    if prim & LEGS:
        return "Legs"
    if "abdominals" in prim:
        return "Core"
    if x.get("force") == "push":
        return "Push"
    if x.get("force") == "pull":
        return "Pull"
    return "Full Body"


def muscles(names: list[str]) -> list[str]:
    out = []
    for m in names or []:
        v = MUSCLE.get(m)
        if v and v not in out:
            out.append(v)
    return out


def photos(src_root: Path, fid: str, rel_images: list[str]) -> list[str]:
    from PIL import Image
    out = []
    for i, rel in enumerate(rel_images[:2]):
        src = src_root / "exercises" / rel
        if not src.exists():
            continue
        dest = OUT_IMG / fid / f"{i}.webp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            if im.width > IMG_WIDTH:
                im = im.resize((IMG_WIDTH, round(im.height * IMG_WIDTH / im.width)), Image.LANCZOS)
            im.save(dest, "WEBP", quality=IMG_QUALITY, method=6)
        out.append(f"/static/exercises/{fid}/{i}.webp")
    return out


def main(src: str) -> None:
    src_root = Path(src)
    db = json.loads((src_root / "dist/exercises.json").read_text())
    by_name = {x["name"]: x for x in db}
    curated = {e["name"]: e for e in EXERCISES}
    curated_norm = {norm(n) for n in curated}
    used = {v for v in CURATED_TO_FEDB.values() if v}
    common_extra = set(EXTRA_COMMON)

    if OUT_IMG.exists():
        for old in OUT_IMG.rglob("*.webp"):
            old.unlink()

    entries = []
    # 1. Curated: enrichment of the existing 169, keyed by the curated name.
    for name in sorted(curated):
        fedb = by_name.get(CURATED_TO_FEDB.get(name) or "")
        steps = (fedb.get("instructions") or []) if fedb else []
        level = fedb.get("level") if fedb else None
        steps_source = "fedb" if steps else None
        if not steps and name in ZENKAI_STEPS:      # no usable match: our own steps
            level, steps = ZENKAI_STEPS[name]
            steps_source = "zenkai"
        entries.append({
            "name": name,
            "source": "curated",
            "fedb_id": fedb["id"] if fedb else None,
            "tier": 1,
            "level": level,
            "instructions": steps,
            "steps_source": steps_source,
            "images": photos(src_root, fedb["id"], fedb.get("images") or []) if fedb else [],
        })
    # 2. The rest of free-exercise-db under its own names.
    skipped = []
    for x in sorted(db, key=lambda x: x["name"]):
        if x["name"] in used:
            continue
        if norm(x["name"]) in curated_norm:
            skipped.append(x["name"])
            continue
        tier = 1 if x["name"] in common_extra else 2
        eq = EQUIPMENT.get(x.get("equipment"), "Other")
        if x["category"] == "cardio" and eq == "Machine":
            eq = "Cardio Machine"
        prim = muscles(x.get("primaryMuscles"))
        sec = [m for m in muscles(x.get("secondaryMuscles")) if m not in prim]
        entries.append({
            "name": x["name"],
            "source": "fedb",
            "fedb_id": x["id"],
            "tier": tier,
            "category": category(x),
            "equipment": eq,
            "muscle_primary": prim,
            "muscle_secondary": sec,
            "log_type": "time" if x["category"] == "stretching" else "reps",
            "level": x.get("level"),
            "instructions": x.get("instructions") or [],
            "steps_source": "fedb" if x.get("instructions") else None,
            "images": photos(src_root, x["id"], x.get("images") or []),
        })

    OUT_JSON.write_text(json.dumps({
        "source": "free-exercise-db (public domain, https://github.com/yuhonas/free-exercise-db)",
        "count": len(entries),
        "exercises": entries,
    }, indent=1, ensure_ascii=False) + "\n")
    t1 = sum(1 for e in entries if e["tier"] == 1)
    size = sum(p.stat().st_size for p in OUT_IMG.rglob("*.webp")) if OUT_IMG.exists() else 0
    print(f"{len(entries)} exercises ({t1} common, {len(entries) - t1} more); "
          f"{sum(len(e['images']) for e in entries)} photos, {size / 1e6:.1f} MB; "
          f"skipped as duplicates of curated names: {len(skipped)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fedb/src")
