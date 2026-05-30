import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.db import get_db
from app.routes.auth import get_current_user
from app.utils import trash

router = APIRouter()


@router.post("/undo/{token}")
async def undo(
    token: str,
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Restore a recently deleted item by its undo token (user-scoped)."""
    uid = current_user["id"]
    async with conn.execute(
        "SELECT kind, payload FROM deleted_items WHERE token = ? AND user_id = ?",
        (token, uid),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Nothing to undo")

    try:
        label = await trash.restore(conn, uid, row["kind"], json.loads(row["payload"]))
    except trash.RestoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Consume the token so it can't be replayed.
    await conn.execute("DELETE FROM deleted_items WHERE token = ?", (token,))
    await conn.commit()
    return JSONResponse({"restored": row["kind"], "label": label})
