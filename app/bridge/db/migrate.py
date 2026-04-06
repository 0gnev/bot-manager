"""
Simple SQL migration runner.

Tracks applied migrations in a ``schema_migrations`` table.
On each call to ``run_migrations`` it scans the ``migrations/`` directory,
sorts by filename, and applies any that haven't been recorded yet.
"""

from __future__ import annotations

import logging
from importlib import resources

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_PACKAGE = "bridge.db.migrations"


async def run_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply pending SQL migrations and return the list of newly applied filenames."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        applied: set[str] = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }

        migration_files = sorted(
            f.name
            for f in resources.files(MIGRATIONS_PACKAGE).iterdir()
            if f.name.endswith(".sql")
        )

        newly_applied: list[str] = []
        for filename in migration_files:
            if filename in applied:
                continue
            sql = resources.files(MIGRATIONS_PACKAGE).joinpath(filename).read_text("utf-8")
            logger.info("Applying migration %s", filename)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", filename
                )
            newly_applied.append(filename)
            logger.info("Applied migration %s", filename)

        if not newly_applied:
            logger.info("Database schema is up to date")

        return newly_applied
