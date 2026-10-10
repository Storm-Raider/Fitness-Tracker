import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.db import get_db, write_tx
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
    try:
        async with write_tx(conn):
            # Consume the token first, inside the transaction: a concurrent
            # replay finds nothing, and a failed restore rolls the token back.
            async with conn.execute(
                "DELETE FROM deleted_items WHERE token = ? AND user_id = ? "
                "RETURNING kind, payload",
                (token, uid),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Nothing to undo")
            label = await trash.restore(conn, uid, row["kind"], json.loads(row["payload"]))
    except trash.RestoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return JSONResponse({"restored": row["kind"], "label": label})
