"""
Idempotency store — prevents duplicate processing of webhooks and Telegram updates.

Hybrid approach: in-memory dict for hot-path speed, PostgreSQL for durability.
On startup, loads unexpired keys from PG. Writes go to both memory and PG.
"""

from __future__ import annotations

import asyncio
import logging
import time

from bridge.db import get_pool

logger = logging.getLogger(__name__)

DEFAULT_TTL = 3600  # 1 hour
CLEANUP_INTERVAL = 600  # 10 minutes

# In-memory store: key -> expiry timestamp (wall clock)
_store: dict[str, float] = {}
_inflight: set[str] = set()
_lock = asyncio.Lock()


async def init(state_path: str = "") -> None:
    """Load persisted entries from PostgreSQL."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT key, expires_at FROM idempotency_keys WHERE expires_at > now()"
    )
    now = time.time()
    for row in rows:
        exp_ts = row["expires_at"].timestamp()
        if exp_ts > now:
            _store[row["key"]] = exp_ts
    logger.info("Idempotency store loaded: %d entries", len(_store))


async def _persist_key(key: str, expires_at_ts: float) -> None:
    """Write a single key to PG (fire-and-forget)."""
    try:
        from datetime import datetime, timezone

        pool = get_pool()
        exp_dt = datetime.fromtimestamp(expires_at_ts, tz=timezone.utc)
        await pool.execute(
            """
            INSERT INTO idempotency_keys (key, expires_at) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET expires_at = EXCLUDED.expires_at
            """,
            key,
            exp_dt,
        )
    except Exception as exc:
        logger.warning("Failed to persist idempotency key %s: %s", key, exc)


async def is_duplicate(key: str) -> bool:
    """Return True if this key was already processed and hasn't expired."""
    expiry = _store.get(key)
    if expiry is None:
        return False
    if time.time() > expiry:
        del _store[key]
        return False
    return True


async def mark_processed(key: str, ttl: float = DEFAULT_TTL) -> None:
    """Record that this key has been processed."""
    exp = time.time() + ttl
    _store[key] = exp
    asyncio.create_task(_persist_key(key, exp))


async def begin_processing(key: str) -> bool:
    """Atomically claim a key for processing. Returns True if duplicate/in-flight."""
    async with _lock:
        if key in _inflight or await is_duplicate(key):
            return True
        _inflight.add(key)
        return False


async def finish_processing(key: str, ttl: float = DEFAULT_TTL) -> None:
    """Mark a claimed key as processed and release its in-flight claim."""
    async with _lock:
        _inflight.discard(key)
        exp = time.time() + ttl
        _store[key] = exp
    asyncio.create_task(_persist_key(key, exp))


async def abandon_processing(key: str) -> None:
    """Release an in-flight claim without marking the key as processed."""
    async with _lock:
        _inflight.discard(key)


async def check_and_mark(key: str, ttl: float = DEFAULT_TTL) -> bool:
    """Atomic check-and-mark. Returns True if duplicate, False if new (and marks it)."""
    async with _lock:
        if key in _inflight or await is_duplicate(key):
            return True
        exp = time.time() + ttl
        _store[key] = exp
    asyncio.create_task(_persist_key(key, exp))
    return False


async def cleanup_expired() -> None:
    """Remove expired entries from memory and PG."""
    now = time.time()
    expired = [k for k, exp in _store.items() if now > exp]
    for k in expired:
        del _store[k]
    if expired:
        logger.debug("Idempotency cleanup: removed %d expired entries from memory", len(expired))
    try:
        pool = get_pool()
        await pool.execute("DELETE FROM idempotency_keys WHERE expires_at < now()")
    except Exception as exc:
        logger.warning("Failed to clean up idempotency keys in PG: %s", exc)


async def run_cleanup_loop() -> None:
    """Background task that calls cleanup_expired periodically."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await cleanup_expired()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Idempotency cleanup error: %s", exc)
