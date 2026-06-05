from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/stats")
async def stats_redirect():
    return RedirectResponse("/analytics", status_code=301)
