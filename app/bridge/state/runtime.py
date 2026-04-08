"""
Runtime automation controls — persisted in PostgreSQL.

Table: runtime_controls (singleton row, id=1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bridge.db import get_pool

logger = logging.getLogger(__name__)

OPERATING_MODES = frozenset({"normal", "degraded", "frozen"})


def normalize_operating_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in OPERATING_MODES:
        return normalized
    return "normal"


def _default_controls() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "global_automation_enabled": True,
        "operating_mode": "normal",
        "tutor_time_zone": None,
        "updated_at": now,
        "updated_by": "system",
        "reason": None,
        "incident_reason": None,
        "incident_started_at": None,
        "incident_started_by": None,
    }


def _row_to_controls(row) -> dict:
    return {
        "global_automation_enabled": row["global_automation_enabled"],
        "operating_mode": normalize_operating_mode(row.get("operating_mode")),
        "tutor_time_zone": row["tutor_time_zone"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
        "reason": row["reason"],
        "incident_reason": row.get("incident_reason"),
        "incident_started_at": row["incident_started_at"].isoformat() if row.get("incident_started_at") else None,
        "incident_started_by": row.get("incident_started_by"),
    }


async def load_controls(state_path: str = "") -> dict:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM runtime_controls WHERE id = 1")
    if row is None:
        return _default_controls()
    return _row_to_controls(row)


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
            operating_mode = CASE WHEN $1 THEN 'normal' ELSE 'degraded' END,
            updated_at = now(),
            updated_by = $2,
            reason = $3,
            incident_reason = CASE WHEN $1 THEN NULL ELSE $3 END,
            incident_started_at = CASE WHEN $1 THEN NULL ELSE now() END,
            incident_started_by = CASE WHEN $1 THEN NULL ELSE $2 END
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
    return _row_to_controls(row)


async def save_tutor_time_zone(
    state_path: str = "",
    *,
    tutor_time_zone: str | None,
    updated_by: str,
    reason: str | None = None,
) -> dict:
    pool = get_pool()
    normalized = (tutor_time_zone or "").strip() or None
    row = await pool.fetchrow(
        """
        UPDATE runtime_controls
        SET tutor_time_zone = $1,
            updated_at = now(),
            updated_by = $2,
            reason = $3
        WHERE id = 1
        RETURNING *
        """,
        normalized,
        updated_by,
        reason,
    )
    if row is None:
        return _default_controls()
    return _row_to_controls(row)


async def save_operating_mode(
    state_path: str = "",
    *,
    operating_mode: str,
    updated_by: str,
    reason: str | None = None,
) -> dict:
    normalized_mode = normalize_operating_mode(operating_mode)
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE runtime_controls
        SET global_automation_enabled = ($1 = 'normal'),
            operating_mode = $1,
            updated_at = now(),
            updated_by = $2,
            reason = $3,
            incident_reason = CASE WHEN $1 = 'normal' THEN NULL ELSE $3 END,
            incident_started_at = CASE WHEN $1 = 'normal' THEN NULL ELSE now() END,
            incident_started_by = CASE WHEN $1 = 'normal' THEN NULL ELSE $2 END
        WHERE id = 1
        RETURNING *
        """,
        normalized_mode,
        updated_by,
        reason,
    )
    if row is None:
        return _default_controls()
    return _row_to_controls(row)
