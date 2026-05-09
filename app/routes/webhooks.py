import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.routes.auth import require_admin

router = APIRouter()


@router.get("/webhooks")
async def get_webhook_config(user=Depends(require_admin)):
    url = os.environ.get("WEBHOOK_URL", "").strip() or None
    return JSONResponse({"url": url, "events": ["pr_achieved"] if url else []})
