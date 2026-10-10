from datetime import date as _date
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound
from pathlib import Path

from app.utils.static_url import static_url

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.globals["static_url"] = static_url


def _human_date(iso_str) -> str:
    """Convert an ISO date string to a human-readable label."""
    if not iso_str:
        return "—"
    try:
        d = _date.fromisoformat(str(iso_str)[:10])
    except (ValueError, TypeError):
        return str(iso_str)[:10]
    today = _date.today()
    delta = (today - d).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    month = d.strftime("%b")
    if d.year == today.year:
        return f"{month} {d.day}"
    return f"{month} {d.day}, {d.year}"


templates.env.filters["human_date"] = _human_date


def _has_template(name: str) -> bool:
    try:
        templates.env.get_template(name)
        return True
    except TemplateNotFound:
        return False


def render(request: Request, template_name: str, data: dict, json_only: bool = False):
    """
    Content negotiation:
      json_only=True  → always JSONResponse (flag wins)
      HX-Request      → partial template ({template_name}_partial.html), or the
                        full page when the page has no partial
      Accept:text/html→ full page template ({template_name}.html)
      default         → JSONResponse
    """
    if json_only:
        return JSONResponse(data)
    context = {**data}
    if request.headers.get("HX-Request"):
        # Not every page has a partial; fall back to the full page rather than
        # raising TemplateNotFound (a 500) on an hx-get or hx-boost.
        partial = f"{template_name}_partial.html"
        return templates.TemplateResponse(
            request, partial if _has_template(partial) else f"{template_name}.html", context
        )
    if "text/html" in request.headers.get("Accept", ""):
        return templates.TemplateResponse(request, f"{template_name}.html", context)
    return JSONResponse(data)
