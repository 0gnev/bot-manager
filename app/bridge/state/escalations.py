"""
Escalation state — persisted in PostgreSQL.

Table: escalations (supports multiple escalations per booking).

Escalation lifecycle:
  pending  → tutor notified, waiting for reply
  resolved → tutor replied, answer sent to student
"""

from __future__ import annotations

import logging
from pathlib import Path

from bridge.db import get_pool

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record to the dict shape consumers expect."""
    data = dict(row)
    for ts_field in ("created_at", "resolved_at"):
        val = data.get(ts_field)
        if val is not None and hasattr(val, "isoformat"):
            data[ts_field] = val.isoformat()
        elif val is None and ts_field == "resolved_at":
            data[ts_field] = None
    # Consumers don't use the BIGSERIAL id directly
    data.pop("id", None)
    return data


async def create(
    state_path: str,
    booking_id: str,
    question: str,
    tutor_message_id: int | None = None,
    reason: str | None = None,
) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO escalations (booking_id, question, tutor_message_id, reason)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        booking_id,
        question,
        tutor_message_id,
        reason,
    )
    data = _row_to_dict(row)
    await _export_to_obsidian(state_path, data)
    return data


async def load(state_path: str, booking_id: str) -> dict | None:
    """Load the latest escalation for a booking (backward-compatible)."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM escalations
        WHERE booking_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        booking_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def resolve(
    state_path: str, booking_id: str, tutor_reply: str, resolved_by: str | None = None
) -> dict | None:
    """Resolve the latest pending escalation for a booking."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE escalations
        SET status = 'resolved',
            tutor_reply = $2,
            resolved_by = $3,
            resolved_at = now()
        WHERE id = (
            SELECT id FROM escalations
            WHERE booking_id = $1 AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
        )
        RETURNING *
        """,
        booking_id,
        tutor_reply,
        resolved_by,
    )
    if row is None:
        return None
    data = _row_to_dict(row)
    await _export_to_obsidian(state_path, data)
    return data


async def find_pending_by_tutor_message(
    state_path: str, tutor_message_id: int
) -> dict | None:
    """Find pending escalation by the message ID sent to tutor."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM escalations
        WHERE tutor_message_id = $1 AND status = 'pending'
        LIMIT 1
        """,
        tutor_message_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def _export_to_obsidian(state_path: str, data: dict) -> None:
    try:
        from obsidian_adapter.writer import export_escalation
        knowledge_path = str(Path(state_path).parent / "knowledge")
        await export_escalation(knowledge_path, data)
    except Exception as exc:
        logger.warning("Failed to export escalation to Obsidian: %s", exc)
