from datetime import date as _date
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


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


def render(request: Request, template_name: str, data: dict, json_only: bool = False):
    """
    Content negotiation:
      json_only=True  → always JSONResponse (flag wins)
      HX-Request      → partial template ({template_name}_partial.html)
      Accept:text/html→ full page template ({template_name}.html)
      default         → JSONResponse
    """
    if json_only:
        return JSONResponse(data)
    context = {**data}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, f"{template_name}_partial.html", context)
    if "text/html" in request.headers.get("Accept", ""):
        return templates.TemplateResponse(request, f"{template_name}.html", context)
    return JSONResponse(data)
