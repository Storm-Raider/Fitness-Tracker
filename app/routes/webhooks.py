import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/webhooks")
async def get_webhook_config():
    url = os.environ.get("WEBHOOK_URL", "").strip() or None
    return JSONResponse({"url": url, "events": ["pr_achieved"] if url else []})
