"""
asyncpg connection pool lifecycle.

Call ``init_pool`` once at startup and ``close_pool`` on shutdown.
State modules obtain connections via ``get_pool()``.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool(database_url: str, *, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(database_url, min_size=min_size, max_size=max_size)
    logger.info("Database pool created (%d–%d connections)", min_size, max_size)
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_pool() first")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")
