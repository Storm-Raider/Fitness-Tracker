import hmac
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.utils.render import templates

router = APIRouter()

COOKIE_NAME = "fittrack_session"
_SALT = "fittrack-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(os.environ["APP_SECRET"], salt=_SALT)


def verify_session(cookie_value: str) -> bool:
    session_days = int(os.environ.get("SESSION_DAYS", "30"))
    try:
        _serializer().loads(cookie_value, max_age=session_days * 86400)
        return True
    except (BadSignature, SignatureExpired):
        return False


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"error": False, "next": next})


@router.post("/login")
async def login_post(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    app_password = os.environ["APP_PASSWORD"]
    if hmac.compare_digest(password.encode(), app_password.encode()):
        session_days = int(os.environ.get("SESSION_DAYS", "30"))
        dest = next if next.startswith("/") else "/"
        response = RedirectResponse(url=dest, status_code=303)
        token = _serializer().dumps("authenticated")
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=session_days * 86400,
            httponly=True,
            samesite="strict",
        )
        return response
    return templates.TemplateResponse(
        request, "login.html", {"error": True, "next": next}, status_code=401
    )


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict")
    return response
