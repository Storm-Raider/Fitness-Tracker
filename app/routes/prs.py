from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/prs")
async def prs_redirect():
    return RedirectResponse("/analytics", status_code=301)
