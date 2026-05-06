from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


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
