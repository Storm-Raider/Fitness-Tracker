import aiosqlite
from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils.pr_utils import fetch_prs
from app.utils.render import render

router = APIRouter()


def _time_ago(iso_date: str | None) -> tuple[str, int | None]:
    """Return (human label, days_ago) for an ISO date string."""
    if not iso_date:
        return "—", None
    days = (date.today() - date.fromisoformat(iso_date)).days
    if days == 0:
        label = "today"
    elif days == 1:
        label = "yesterday"
    elif days < 7:
        label = f"{days}d ago"
    elif days < 30:
        label = f"{days // 7}w ago"
    elif days < 365:
        label = f"{days // 30}mo ago"
    else:
        label = f"{days // 365}y ago"
    return label, days


@router.get("/prs", response_class=HTMLResponse)
async def personal_records(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    uid = current_user["id"]

    records = await fetch_prs(conn, uid)

    for r in records:
        r["time_ago"], r["days_ago"] = _time_ago(r["pr_date"])

    return render(
        request,
        "prs",
        {"records": records, "user": dict(current_user)},
    )
