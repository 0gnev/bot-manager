"""
Runtime automation controls — persisted in PostgreSQL.

Table: runtime_controls (singleton row, id=1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bridge.db import get_pool

logger = logging.getLogger(__name__)


def _default_controls() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "global_automation_enabled": True,
        "updated_at": now,
        "updated_by": "system",
        "reason": None,
    }


async def load_controls(state_path: str = "") -> dict:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM runtime_controls WHERE id = 1")
    if row is None:
        return _default_controls()
    return {
        "global_automation_enabled": row["global_automation_enabled"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
        "reason": row["reason"],
    }


async def save_controls(
    state_path: str = "",
    *,
    global_automation_enabled: bool,
    updated_by: str,
    reason: str | None = None,
) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE runtime_controls
        SET global_automation_enabled = $1,
            updated_at = now(),
            updated_by = $2,
            reason = $3
        WHERE id = 1
        RETURNING *
        """,
        global_automation_enabled,
        updated_by,
        reason,
    )
    if row is None:
        # Shouldn't happen — migration inserts the singleton row
        return _default_controls()
    return {
        "global_automation_enabled": row["global_automation_enabled"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
        "reason": row["reason"],
    }
