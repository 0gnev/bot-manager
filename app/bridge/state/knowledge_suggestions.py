"""
Knowledge suggestion state — persisted in PostgreSQL.

Suggestions are generated from tutor-approved answers but only become static
knowledge after explicit tutor approval.
"""

from __future__ import annotations

import logging

from bridge.db import get_pool

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    data = dict(row)
    for ts_field in ("created_at", "resolved_at"):
        value = data.get(ts_field)
        if value is not None and hasattr(value, "isoformat"):
            data[ts_field] = value.isoformat()
        elif value is None:
            data[ts_field] = None
    return data


async def create(
    state_path: str,
    *,
    source_kind: str,
    booking_id: str | None = None,
    contact_id: int | None = None,
    approval_id: str | None = None,
    escalation_id: int | None = None,
    source_question: str | None = None,
    answer_text: str,
    title: str,
    rationale: str | None,
    content_markdown: str,
    suggested_file_path: str | None,
) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO knowledge_suggestions (
            source_kind,
            booking_id,
            contact_id,
            approval_id,
            escalation_id,
            source_question,
            answer_text,
            title,
            rationale,
            content_markdown,
            suggested_file_path
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING *
        """,
        source_kind,
        booking_id,
        contact_id,
        approval_id,
        escalation_id,
        source_question,
        answer_text,
        title,
        rationale,
        content_markdown,
        suggested_file_path,
    )
    return _row_to_dict(row)


async def load(state_path: str, suggestion_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM knowledge_suggestions WHERE id = $1",
        suggestion_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def set_tutor_message_id(state_path: str, suggestion_id: int, message_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE knowledge_suggestions
        SET tutor_message_id = $2
        WHERE id = $1
        """,
        suggestion_id,
        message_id,
    )


async def resolve(
    state_path: str,
    suggestion_id: int,
    *,
    status: str,
    resolved_by: str,
    knowledge_file_path: str | None = None,
) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE knowledge_suggestions
        SET status = $2,
            resolved_by = $3,
            knowledge_file_path = COALESCE($4, knowledge_file_path),
            resolved_at = now()
        WHERE id = $1
        RETURNING *
        """,
        suggestion_id,
        status,
        resolved_by,
        knowledge_file_path,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def list_pending(state_path: str) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM knowledge_suggestions
        WHERE status = 'pending'
        ORDER BY created_at
        """
    )
    return [_row_to_dict(row) for row in rows]
