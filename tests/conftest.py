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
#
# Only tests that explicitly request the ``pg_pool`` fixture will trigger a
# database connection.  Tests that don't need PostgreSQL (policy engine,
# message routing, etc.) continue to run without any database.

_DEFAULT_DB_URL = "postgresql://bridge:bridge@localhost:5432/bridge_test"


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)


# Single event loop shared by all PG-backed tests.
_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro):
    """Run an async coroutine on the shared session event loop."""
    assert _loop is not None, "Session event loop not initialized — request the pg_pool fixture"
    return _loop.run_until_complete(coro)


@pytest.fixture(scope="session")
def database_url():
    return _get_database_url()


@pytest.fixture(scope="session")
def pg_pool(database_url):
    """Create a pool, run migrations, and yield it for the entire test session.

    This fixture is NOT autouse — only tests that declare ``pg_pool`` in their
    signature (or use ``db_clean``) will connect to PostgreSQL.
    """
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


@pytest.fixture()
def db_clean(pg_pool):
    """Truncate all data tables for test isolation.

    Request this fixture (or ``pg_pool``) from any test that writes to the
    database.  It is NOT autouse so non-DB tests stay fast and dependency-free.
    """
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
                    tutor_time_zone = NULL,
                    updated_by = 'system',
                    reason = NULL,
                    updated_at = now()
                WHERE id = 1
            """)

    run_async(_truncate())
