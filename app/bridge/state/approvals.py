"""
Approval state — persisted in PostgreSQL.

Table: approvals

Approval lifecycle:
  pending  -> AI draft sent to tutor for review
  approved -> tutor approved, draft sent to student
  rejected -> tutor rejected, draft discarded
  edited   -> tutor edited and approved, edited version sent to student
"""

from __future__ import annotations

import logging
import uuid

from bridge.db import get_pool

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    data = dict(row)
    for ts_field in ("created_at", "resolved_at"):
        val = data.get(ts_field)
        if val is not None and hasattr(val, "isoformat"):
            data[ts_field] = val.isoformat()
        elif val is None and ts_field == "resolved_at":
            data[ts_field] = None
    return data


async def create_approval(
    state_path: str,
    booking_id: str | None,
    *,
    contact_id: int | None = None,
    student_chat_id: int,
    draft_content: str,
    action: str,
    confidence: float,
) -> dict:
    approval_id = uuid.uuid4().hex[:12]
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO approvals (
            approval_id, booking_id, contact_id, student_chat_id,
            draft_content, action, confidence
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        approval_id,
        booking_id,
        contact_id,
        student_chat_id,
        draft_content,
        action,
        confidence,
    )
    logger.info(
        "Created approval %s for booking=%s contact=%s",
        approval_id,
        booking_id,
        contact_id,
    )
    return _row_to_dict(row)


async def get_approval(state_path: str, approval_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM approvals WHERE approval_id = $1", approval_id
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def resolve_approval(
    state_path: str,
    approval_id: str,
    status: str,
    final_content: str | None = None,
    reviewer: str | None = None,
    review_channel: str | None = None,
) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE approvals
        SET status = $2,
            draft_content = COALESCE($3, draft_content),
            reviewer = COALESCE($4, reviewer),
            review_channel = COALESCE($5, review_channel),
            resolved_at = now()
        WHERE approval_id = $1
        RETURNING *
        """,
        approval_id,
        status,
        final_content,
        reviewer,
        review_channel,
    )
    if row is None:
        return None
    logger.info("Resolved approval %s -> %s", approval_id, status)
    return _row_to_dict(row)


async def set_tutor_message_id(
    state_path: str, approval_id: str, message_id: int
) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE approvals SET tutor_message_id = $1 WHERE approval_id = $2",
        message_id,
        approval_id,
    )


async def list_pending(
    state_path: str,
    booking_id: str | None = None,
    *,
    contact_id: int | None = None,
) -> list[dict]:
    pool = get_pool()
    if booking_id:
        rows = await pool.fetch(
            """
            SELECT * FROM approvals
            WHERE status = 'pending' AND booking_id = $1
            ORDER BY created_at
            """,
            booking_id,
        )
    elif contact_id is not None:
        rows = await pool.fetch(
            """
            SELECT * FROM approvals
            WHERE status = 'pending' AND contact_id = $1
            ORDER BY created_at
            """,
            contact_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at"
        )
    return [_row_to_dict(r) for r in rows]


async def find_pending_by_tutor_message(
    state_path: str, tutor_message_id: int
) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM approvals
        WHERE tutor_message_id = $1 AND status = 'pending'
        LIMIT 1
        """,
        tutor_message_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)
