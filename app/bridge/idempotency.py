"""
Idempotency store — prevents duplicate processing of webhooks and Telegram updates.

Uses an in-memory dict with TTL for fast lookup.
Persists to disk (data/state/idempotency.json) for crash recovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TTL = 3600  # 1 hour
CLEANUP_INTERVAL = 600  # 10 minutes

# In-memory store: key -> expiry timestamp (wall clock)
_store: dict[str, float] = {}
_inflight: set[str] = set()
_state_path: str | None = None
_lock = asyncio.Lock()


def _file_path() -> Path | None:
    if _state_path is None:
        return None
    return Path(_state_path) / "idempotency.json"


async def init(state_path: str) -> None:
    """Load persisted entries from disk."""
    global _state_path
    _state_path = state_path
    fp = _file_path()
    if fp is None or not fp.exists():
        return
    try:
        text = await asyncio.to_thread(fp.read_text, "utf-8")
        data: dict[str, float] = json.loads(text)
        now = time.time()
        for key, expiry in data.items():
            if expiry > now:
                _store[key] = expiry
        logger.info("Idempotency store loaded: %d entries", len(_store))
    except Exception as exc:
        logger.warning("Failed to load idempotency state: %s", exc)


async def _persist() -> None:
    """Write current entries to disk (absolute expiry timestamps)."""
    fp = _file_path()
    if fp is None:
        return
    now = time.time()
    data = {k: round(exp, 1) for k, exp in _store.items() if exp > now}
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            fp.write_text,
            json.dumps(data, ensure_ascii=False),
            "utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to persist idempotency state: %s", exc)


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
    _store[key] = time.time() + ttl
    asyncio.create_task(_persist())


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
        _store[key] = time.time() + ttl
    asyncio.create_task(_persist())


async def abandon_processing(key: str) -> None:
    """Release an in-flight claim without marking the key as processed."""
    async with _lock:
        _inflight.discard(key)


async def check_and_mark(key: str, ttl: float = DEFAULT_TTL) -> bool:
    """Atomic check-and-mark. Returns True if duplicate, False if new (and marks it)."""
    async with _lock:
        if key in _inflight or await is_duplicate(key):
            return True
        _store[key] = time.time() + ttl
    asyncio.create_task(_persist())
    return False


async def cleanup_expired() -> None:
    """Remove entries older than their TTL."""
    now = time.time()
    expired = [k for k, exp in _store.items() if now > exp]
    for k in expired:
        del _store[k]
    if expired:
        logger.debug("Idempotency cleanup: removed %d expired entries", len(expired))
        await _persist()


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
