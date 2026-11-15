"""app/static/icons.js holds only the Lucide icons the app uses
(scripts/icons/build.py). A page that references one it lacks would show a gap."""
from pathlib import Path

from scripts.icons.build import built_icons, used_icons

ROOT = Path(__file__).resolve().parent.parent


def test_every_icon_the_app_uses_is_in_the_built_file():
    missing = used_icons() - built_icons()
    assert not missing, f"run python3 scripts/icons/build.py (missing: {sorted(missing)})"


def test_built_file_has_no_unused_icons():
    extra = built_icons() - used_icons()
    assert not extra, f"run python3 scripts/icons/build.py (no longer used: {sorted(extra)})"


def test_scan_finds_the_dynamic_cases():
    used = used_icons()
    assert {"search-x", "dumbbell"} <= used          # both branches of a Jinja {% if %}
    assert "trophy" in used                          # an achievement's "icon" value in Python
    assert len(used) > 40


def test_the_full_bundle_is_not_served():
    assert not (ROOT / "app/static/lucide.min.js").exists()
    assert 'src="/static/icons.js"' in (ROOT / "app/templates/base.html").read_text()
    assert (ROOT / "app/static/icons.js").stat().st_size < 40 * 1024
