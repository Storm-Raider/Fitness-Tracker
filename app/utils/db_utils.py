import aiosqlite
from fastapi import HTTPException


async def require_owns(conn: aiosqlite.Connection, table: str, row_id: int, uid: int) -> None:
    async with conn.execute(
        f"SELECT id FROM {table} WHERE id = ? AND user_id = ?",
        (row_id, uid),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404)
