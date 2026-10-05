"""Content-hashed URLs for /static files.

sw.js serves /static/ cache-first, so a changed file under an unchanged URL
would be served stale from the service-worker cache indefinitely. Templates
use {{ static_url('plan_view.js') }}, which appends ?v=<hash of the content>;
a changed file gets a new URL and the old cache entry is simply never asked
for again. The hash is cached per (mtime, size) so editing a file during
development is picked up without a restart.
"""

import hashlib
import logging
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "static"

_cache: dict[str, tuple[tuple[int, int], str]] = {}


def static_url(name: str) -> str:
    path = (STATIC_DIR / name).resolve()
    if STATIC_DIR.resolve() not in path.parents or not path.is_file():
        logging.warning("static_url: %s is not a file under app/static", name)
        return f"/static/{name}"
    st = path.stat()
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(name)
    if hit and hit[0] == stamp:
        return f"/static/{name}?v={hit[1]}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    _cache[name] = (stamp, digest)
    return f"/static/{name}?v={digest}"
