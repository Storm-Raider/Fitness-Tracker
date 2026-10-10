"""Charts follow DESIGN.md's colour rules: blue (#4f9cf9) is chrome, gold
(#f59e0b) is a PR, and a series keeps its colour whatever its rank."""
import re

from app.utils.charts import MUSCLE_COLORS, generate_muscle_bars, generate_weekly_bar_chart

BLUE, GOLD, DATA = "#4f9cf9", "#f59e0b", "#9085e9"


def _fill_for(svg: str, muscle: str) -> str:
    """The fill of the bar whose tooltip names `muscle` (not its grey track)."""
    m = re.search(rf'fill="(#[0-9a-fA-F]{{6}})"[^>]*><title>{muscle}:', svg)
    assert m, f"no bar for {muscle}"
    return m.group(1)


def test_a_muscle_keeps_its_colour_whatever_its_rank():
    top = generate_muscle_bars([("Chest", 100.0), ("Back", 50.0)])
    second = generate_muscle_bars([("Back", 100.0), ("Chest", 50.0)])
    assert _fill_for(top, "Chest") == _fill_for(second, "Chest") == MUSCLE_COLORS["Chest"]


def test_muscle_bars_use_neither_blue_nor_gold():
    svg = generate_muscle_bars([(m, 10.0 * (i + 1)) for i, m in enumerate(MUSCLE_COLORS)])
    assert BLUE not in svg.lower() and GOLD not in svg.lower()
    assert BLUE not in {c.lower() for c in MUSCLE_COLORS.values()}


def test_weekly_volume_bars_use_the_data_colour():
    svg = generate_weekly_bar_chart([(f"2026-10-0{d}", 100.0 * d) for d in range(1, 8)])
    assert BLUE not in svg.lower() and DATA in svg.lower()


async def test_the_muscle_map_gets_the_same_colours(client, db):
    w = (await client.post("/workouts", json={})).json()["id"]
    html = (await client.get(f"/workouts/{w}", headers={"Accept": "text/html"})).text
    assert '"Chest": "' + MUSCLE_COLORS["Chest"] + '"' in html
    assert "'Chest':     '#4f9cf9'" not in html
