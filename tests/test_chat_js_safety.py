"""Static guard: no HTML-string APIs in the JS that renders model/database text.

plan_view.js, sheet.js and coach_chat.js display text that came from a model or
the database (exercise names, notes, replies). They build the DOM with
createElement + textContent so that text can never become markup. This test
fails the build if one of the APIs that parse a string as HTML (or run it as
code) appears. A reviewed constant-string use can be exempted by putting
`xss-ok:` and a reason in a comment on the same line.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parent.parent / "app" / "static"
GUARDED = ["plan_view.js", "sheet.js", "coach_chat.js"]
REQUIRED = {"plan_view.js", "sheet.js"}  # coach_chat.js arrives with the chat itself

FORBIDDEN = re.compile(
    r"\.innerHTML\b|\.outerHTML\b|insertAdjacentHTML|document\.write(ln)?\s*\(|"
    r"\beval\s*\(|new\s+Function\s*\(|setTimeout\s*\(\s*['\"`]|DOMParser|createContextualFragment"
)


def _scan(text: str) -> list[tuple[int, str]]:
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        if "xss-ok:" in line:
            continue
        if FORBIDDEN.search(line):
            hits.append((n, line.strip()))
    return hits


@pytest.mark.parametrize("name", GUARDED)
def test_no_html_string_apis(name):
    path = STATIC / name
    if not path.exists():
        assert name not in REQUIRED, f"{name} is missing"
        pytest.skip(f"{name} does not exist yet")
    assert _scan(path.read_text()) == [], f"{name} uses an HTML-string API"


def test_the_scanner_catches_each_forbidden_api():
    for bad in [
        "el.innerHTML = x;", "el.outerHTML = x;", "el.insertAdjacentHTML('beforeend', x)",
        "document.write(x)", "eval(x)", "new Function('return 1')", "setTimeout('go()', 5)",
        "new DOMParser()", "range.createContextualFragment(x)",
    ]:
        assert _scan(bad), bad
    assert _scan("el.textContent = x; // no markup") == []
    assert _scan("el.innerHTML = '<b>ok</b>'; // xss-ok: constant string, reviewed") == []
