"""Charts follow DESIGN.md (Daylight): a single series is ink, today's bar may
be signal orange, amber is reserved for records, and a muscle keeps its colour
whatever its rank. The muscle palette is validated for the white card."""
import re
from datetime import date, timedelta

from app.utils.charts import (
    MUSCLE_COLORS,
    _OTHER_MUSCLE,
    generate_muscle_bars,
    generate_weekly_bar_chart,
)

INK, ORANGE, AMBER, CARD = "#141414", "#ff5a1f", "#8c5a00", "#ffffff"
OLD_DARK = ("#9085e9", "#1e2334", "#151c2c", "#5a6a82", "#7a8a9a", "#4f9cf9", "#f59e0b")


def _lum(hex_: str) -> float:
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(int(hex_[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _fill_for(svg: str, muscle: str) -> str:
    """The fill of the bar whose tooltip names `muscle` (not its grey track)."""
    m = re.search(rf'fill="(#[0-9a-fA-F]{{6}})"[^>]*><title>{muscle}:', svg)
    assert m, f"no bar for {muscle}"
    return m.group(1)


def test_a_muscle_keeps_its_colour_whatever_its_rank():
    top = generate_muscle_bars([("Chest", 100.0), ("Back", 50.0)])
    second = generate_muscle_bars([("Back", 100.0), ("Chest", 50.0)])
    assert _fill_for(top, "Chest") == _fill_for(second, "Chest") == MUSCLE_COLORS["Chest"]


def test_muscle_palette_is_distinct_and_avoids_reserved_roles():
    colours = [c.lower() for c in MUSCLE_COLORS.values()]
    assert len(MUSCLE_COLORS) == 8 and len(set(colours)) == 8
    assert not {ORANGE, AMBER, INK} & set(colours)
    assert _OTHER_MUSCLE.lower() not in colours


def test_muscle_marks_read_on_the_white_card():
    # dataviz contrast check: every mark >= 3:1 against #ffffff (the old
    # dark-surface palette had five muscles below 3:1 on white)
    for muscle, colour in MUSCLE_COLORS.items():
        assert _contrast(colour, CARD) >= 3.0, muscle
    assert _contrast(_OTHER_MUSCLE, CARD) >= 3.0


def test_muscle_bars_have_light_tracks_and_readable_labels():
    svg = generate_muscle_bars([(m, 10.0 * (i + 1)) for i, m in enumerate(MUSCLE_COLORS)])
    assert not any(c in svg.lower() for c in OLD_DARK)
    sizes = [float(s) for s in re.findall(r'font-size="([\d.]+)"', svg)]
    assert sizes and min(sizes) >= 18  # >=13px at phone width (viewBox 420 -> ~320px)
    for fill in re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]{6})"', svg):
        assert _contrast(fill, CARD) >= 4.5


TRACE = "#1e5bb8"   # plotted data (Instrument Sans redesign, 2026-10-31)
GRAPHITE = "#23262b"


def test_weekly_volume_bars_are_trace_blue_and_today_is_ink():
    today = date.today()
    days = [((today - timedelta(days=6 - i)).isoformat(), 100.0 * (i + 1)) for i in range(7)]
    svg = generate_weekly_bar_chart(days)
    bar_fills = re.findall(r'<rect[^>]*fill="(#[0-9a-fA-F]{6})"><title>', svg)
    assert bar_fills[:-1] == [TRACE] * 6 and bar_fills[-1] == GRAPHITE
    assert not any(c in svg.lower() for c in OLD_DARK)
    assert ORANGE not in svg.lower()
    assert min(float(x) for x in re.findall(r'font-size="([\d.]+)"', svg)) >= 18


def test_weekly_volume_bars_without_today_are_all_trace_blue():
    svg = generate_weekly_bar_chart([(f"2020-10-0{d}", 100.0 * d) for d in range(1, 8)])
    assert TRACE in svg.lower() and ORANGE not in svg.lower()


async def test_the_muscle_map_gets_the_same_colours(client, db):
    w = (await client.post("/workouts", json={})).json()["id"]
    html = (await client.get(f"/workouts/{w}", headers={"Accept": "text/html"})).text
    assert '"Chest": "' + MUSCLE_COLORS["Chest"] + '"' in html
    assert "'Chest':     '#4f9cf9'" not in html

