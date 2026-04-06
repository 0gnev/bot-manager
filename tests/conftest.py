from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── PostgreSQL fixtures ──────────────────────────────────────────────────────

_DEFAULT_DB_URL = "postgresql://bridge:bridge@localhost:5432/bridge_test"


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)


# Single event loop shared across the entire test session.
# Tests call ``run_async(coro)`` to execute coroutines on this loop.
_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro):
    """Run an async coroutine on the shared session event loop."""
    assert _loop is not None, "Session event loop not initialized"
    return _loop.run_until_complete(coro)


@pytest.fixture(scope="session")
def database_url():
    return _get_database_url()


@pytest.fixture(scope="session", autouse=True)
def pg_pool(database_url):
    """Create a pool, run migrations, and yield it for the entire test session."""
    global _loop
    import asyncpg
    from bridge.db import pool as pool_mod
    from bridge.db.migrate import run_migrations

    _loop = asyncio.new_event_loop()

    async def _setup():
        p = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        pool_mod._pool = p
        await run_migrations(p)
        return p

    pool = _loop.run_until_complete(_setup())
    yield pool
    _loop.run_until_complete(pool.close())
    pool_mod._pool = None
    _loop.close()
    _loop = None


@pytest.fixture(autouse=True)
def db_clean(pg_pool):
    """Truncate all data tables before each test for isolation."""
    async def _truncate():
        async with pg_pool.acquire() as conn:
            await conn.execute("""
                TRUNCATE
                    idempotency_keys,
                    knowledge_updates,
                    attachments,
                    approvals,
                    escalations,
                    messages,
                    bookings,
                    contacts
                CASCADE
            """)
            await conn.execute("""
                UPDATE runtime_controls SET
                    global_automation_enabled = TRUE,
                    updated_by = 'system',
                    reason = NULL,
                    updated_at = now()
                WHERE id = 1
            """)

    run_async(_truncate())
