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


@pytest.fixture(scope="session")
def database_url():
    return _get_database_url()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def pg_pool(database_url, event_loop):
    """Create a pool, run migrations, and yield it for the entire test session."""
    import asyncpg
    from bridge.db import pool as pool_mod
    from bridge.db.migrate import run_migrations

    async def _setup():
        p = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        # Override the module-level pool so state modules use the test DB
        pool_mod._pool = p
        await run_migrations(p)
        return p

    p = event_loop.run_until_complete(_setup())
    yield p
    event_loop.run_until_complete(p.close())
    pool_mod._pool = None


@pytest.fixture(autouse=True)
def db_clean(pg_pool, event_loop):
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
            # Reset runtime controls to defaults
            await conn.execute("""
                UPDATE runtime_controls SET
                    global_automation_enabled = TRUE,
                    updated_by = 'system',
                    reason = NULL,
                    updated_at = now()
                WHERE id = 1
            """)

    event_loop.run_until_complete(_truncate())
