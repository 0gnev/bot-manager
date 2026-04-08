"""
Normalized delivery records backed by PostgreSQL.
"""

from __future__ import annotations

import json
from typing import Any

from bridge.db import get_pool


def _row_to_dict(row) -> dict:
    data = dict(row)
    for ts_field in ("created_at", "updated_at"):
        val = data.get(ts_field)
        if val is not None and hasattr(val, "isoformat"):
            data[ts_field] = val.isoformat()
    payload = data.get("payload")
    if isinstance(payload, str):
        try:
            data["payload"] = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            pass
    return data


async def record_conn(
    conn,
    *,
    message_id: int,
    contact_id: int | None,
    booking_id: str | None,
    direction: str,
    transport: str,
    source: str | None = None,
    status: str = "recorded",
    chat_id: int | None = None,
    transport_message_id: int | None = None,
    attempts: int = 1,
    recipient: str | None = None,
    error_text: str | None = None,
    payload: dict | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO deliveries (
            message_id,
            contact_id,
            booking_id,
            direction,
            transport,
            source,
            status,
            chat_id,
            transport_message_id,
            attempts,
            recipient,
            error_text,
            payload,
            updated_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, now()
        )
        ON CONFLICT (message_id, transport) DO UPDATE SET
            contact_id = COALESCE(EXCLUDED.contact_id, deliveries.contact_id),
            booking_id = COALESCE(EXCLUDED.booking_id, deliveries.booking_id),
            direction = EXCLUDED.direction,
            source = COALESCE(EXCLUDED.source, deliveries.source),
            status = EXCLUDED.status,
            chat_id = COALESCE(EXCLUDED.chat_id, deliveries.chat_id),
            transport_message_id = COALESCE(EXCLUDED.transport_message_id, deliveries.transport_message_id),
            attempts = GREATEST(EXCLUDED.attempts, deliveries.attempts),
            recipient = COALESCE(EXCLUDED.recipient, deliveries.recipient),
            error_text = COALESCE(EXCLUDED.error_text, deliveries.error_text),
            payload = COALESCE(EXCLUDED.payload, deliveries.payload),
            updated_at = now()
        """,
        message_id,
        contact_id,
        booking_id,
        direction,
        transport,
        source,
        status,
        chat_id,
        transport_message_id,
        attempts,
        recipient,
        error_text,
        json.dumps(payload) if payload is not None else None,
    )


async def list_for_message(state_path: str, message_id: int) -> list[dict]:
    rows = await get_pool().fetch(
        """
        SELECT *
        FROM deliveries
        WHERE message_id = $1
        ORDER BY created_at, id
        """,
        message_id,
    )
    return [_row_to_dict(row) for row in rows]
