import aiosqlite
from pathlib import Path

_conn: aiosqlite.Connection | None = None

SCHEMA = Path(__file__).parent.parent / "schema.sql"


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA.read_text())
    await conn.commit()


async def open_db(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await init_db(conn)
    return conn


async def get_db() -> aiosqlite.Connection:
    """FastAPI dependency — yields the shared connection."""
    assert _conn is not None, "DB not initialised; call set_db() from lifespan"
    yield _conn


def set_db(conn: aiosqlite.Connection) -> None:
    global _conn
    _conn = conn


def clear_db() -> None:
    global _conn
    _conn = None
