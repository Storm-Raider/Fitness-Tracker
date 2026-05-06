import aiosqlite


async def get_or_create_exercise(conn: aiosqlite.Connection, name: str) -> int:
    """Return exercise id for name, creating it if it doesn't exist."""
    await conn.execute(
        "INSERT OR IGNORE INTO exercises(name) VALUES (?)", (name,)
    )
    async with conn.execute(
        "SELECT id FROM exercises WHERE name = ?", (name,)
    ) as cur:
        row = await cur.fetchone()
    return row["id"]
