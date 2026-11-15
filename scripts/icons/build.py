"""Builds app/static/icons.js: the Lucide icons Zenkai actually uses.

  python3 scripts/icons/build.py          (needs node, on a dev machine only)

The full Lucide bundle (scripts/icons/lucide.full.min.js, ISC licence, ~1,950
icons, 400 KB) used to be served to every page. used_icons() scans the app for
the names it references and this script copies just those into a file with the
same `lucide.createIcons()` call the pages already make.

Adding an icon: use it in a template (data-lucide="name") or as an "icon"
value in Python, then re-run this. tests/test_icons.py fails if a page
references an icon the built file doesn't have.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL = ROOT / "scripts/icons/lucide.full.min.js"
OUT = ROOT / "app/static/icons.js"

_ATTR = re.compile(r"""data-lucide=\\?["']([^"']*)["']""")
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_PY_ICON = re.compile(r"""["']icon["']\s*:\s*["']([a-z][a-z0-9-]*)["']""")


def used_icons() -> set[str]:
    """Every icon name the app can render: literal data-lucide values (including
    each branch of a Jinja {% if %} inside one) and "icon" values in Python
    (achievements pass theirs through {{ ach.icon }})."""
    names: set[str] = set()
    files = [p for ext in ("html", "js", "py") for p in (ROOT / "app").rglob(f"*.{ext}")]
    for path in files:
        if path == OUT:
            continue
        text = path.read_text(encoding="utf-8")
        for value in _ATTR.findall(text):
            if "{{" in value:
                continue                               # filled in from Python data
            for part in re.split(r"\{%.*?%\}", value):
                if _NAME.match(part.strip()):
                    names.add(part.strip())
        if path.suffix == ".py":
            names.update(_PY_ICON.findall(text))
    return names


def built_icons() -> set[str]:
    """The names in the built file (first line of its ICONS table per icon)."""
    return set(re.findall(r'^  "([a-z][a-z0-9-]*)": ', OUT.read_text(encoding="utf-8"), re.M))


_RUNTIME = r"""
  // Same result as Lucide's createIcons(): each <i data-lucide="name"> becomes
  // an <svg class="lucide lucide-name ..."> that keeps the element's own
  // attributes (class, style, aria-*).
  var NS = "http://www.w3.org/2000/svg";
  var DEFAULTS = { xmlns: NS, width: 24, height: 24, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" };
  function node(def) {
    var el = document.createElementNS(NS, def[0]);
    for (var k in def[1]) el.setAttribute(k, String(def[1][k]));
    return el;
  }
  function replace(el) {
    var name = el.getAttribute("data-lucide"), icon = ICONS[name];
    if (!icon) { console.warn("icon not in app/static/icons.js (run scripts/icons/build.py): " + name); return; }
    var svg = document.createElementNS(NS, "svg"), k, labelled = false, classes = ["lucide", "lucide-" + name];
    for (k in DEFAULTS) svg.setAttribute(k, String(DEFAULTS[k]));
    svg.setAttribute("data-lucide", name);
    Array.prototype.forEach.call(el.attributes, function (a) {
      if (a.name === "class") {
        a.value.split(" ").forEach(function (c) { if (c && classes.indexOf(c) < 0) classes.push(c); });
      } else {
        svg.setAttribute(a.name, a.value);
        if (a.name === "role" || a.name === "title" || a.name.indexOf("aria-") === 0) labelled = true;
      }
    });
    if (!labelled) svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", classes.join(" "));
    icon.forEach(function (def) { svg.appendChild(node(def)); });
    if (el.parentNode) el.parentNode.replaceChild(svg, el);
  }
  window.lucide = {
    icons: ICONS,
    createIcons: function (opts) {
      var root = (opts && opts.root) || document;
      Array.prototype.forEach.call(root.querySelectorAll("[data-lucide]"), function (el) {
        if (el.namespaceURI !== NS) replace(el);
      });
    }
  };
"""


def main() -> None:
    names = sorted(used_icons())
    dump = subprocess.run(
        ["node", "-e", """
const l = require(process.argv[1]);
const pascal = s => s.replace(/(^|-)([a-z0-9])/g, (m, a, b) => b.toUpperCase());
const out = {}, missing = [];
for (const n of JSON.parse(process.argv[2])) { const i = l.icons[pascal(n)]; if (i) out[n] = i; else missing.push(n); }
console.log(JSON.stringify({ out, missing }));
""", str(FULL), json.dumps(names)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(dump.stdout)
    if data["missing"]:
        sys.exit(f"not Lucide icons: {', '.join(data['missing'])}")
    version = re.search(r"lucide v([\d.]+)", FULL.read_text(encoding="utf-8")[:200]).group(1)
    table = ",\n".join(f'  "{n}": {json.dumps(data["out"][n], separators=(",", ":"))}' for n in names)
    OUT.write_text(
        f"/* Icons from Lucide v{version} (ISC licence, https://lucide.dev): the {len(names)} Zenkai uses.\n"
        "   Generated by scripts/icons/build.py. Do not edit; re-run it to add an icon. */\n"
        "(function () {\n  var ICONS = {\n" + table + "\n  };\n" + _RUNTIME + "})();\n",
        encoding="utf-8",
    )
    print(f"{OUT.relative_to(ROOT)}: {len(names)} icons, {OUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
