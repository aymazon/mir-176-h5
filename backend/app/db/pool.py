"""asyncpg connection pool for the game server.

The pool is created once per process (``init_pool``) and closed on shutdown
(``close_pool``); both are safe to call more than once. Call sites obtain the
live pool through ``get_pool``. Defaults match the single-process game server
layout (one world, serial per-character writes): asyncpg defaults of 10
connections are appropriate for the MVP concurrency targets.
"""

import asyncpg

from app.config import Settings

_pool: asyncpg.Pool | None = None


class PoolNotInitializedError(RuntimeError):
    """Raised when ``get_pool`` is called before ``init_pool`` completes."""


async def init_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Create (or return the existing) connection pool.

    ``dsn`` defaults to ``Settings().database_url``.
    """
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(dsn or Settings().database_url)
    return _pool


async def close_pool() -> None:
    """Close the pool if one is open; no-op otherwise."""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the live pool, or raise ``PoolNotInitializedError`` if absent."""
    if _pool is None:
        raise PoolNotInitializedError(
            "database pool is not initialized; call init_pool() first"
        )
    return _pool
