"""
Normalized contact channels backed by PostgreSQL.
"""

from __future__ import annotations

import json
from typing import Any

from bridge.db import get_pool


def normalize_channel_value(channel_type: str, value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if channel_type == "telegram_user_id":
        return text
    if channel_type == "telegram_username":
        return text.lstrip("@").lower()
    if channel_type == "email":
        return text.lower()
    if channel_type == "phone":
        filtered = "".join(ch for ch in text if ch.isdigit() or ch == "+")
        return filtered or None
    return text


def _row_to_dict(row) -> dict:
    data = dict(row)
    for ts_field in ("created_at", "updated_at", "verified_at"):
        val = data.get(ts_field)
        if val is not None and hasattr(val, "isoformat"):
            data[ts_field] = val.isoformat()
    metadata = data.get("metadata")
    if isinstance(metadata, str):
        try:
            data["metadata"] = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            pass
    return data


async def sync_contact_channels_conn(
    conn,
    contact_id: int,
    *,
    telegram_user_id: int | None = None,
    telegram_username: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> None:
    channels = [
        ("telegram_user_id", telegram_user_id, telegram_user_id is not None),
        ("telegram_username", telegram_username, bool((telegram_username or "").strip())),
        ("email", email, bool((email or "").strip())),
        ("phone", phone, bool((phone or "").strip())),
    ]
    for channel_type, raw_value, is_primary in channels:
        normalized = normalize_channel_value(channel_type, raw_value)
        if normalized is None:
            continue
        channel_value = str(raw_value).strip()
        metadata = {"source": "runtime_sync"}
        await conn.execute(
            """
            INSERT INTO contact_channels (
                contact_id,
                channel_type,
                channel_value,
                normalized_value,
                is_primary,
                verified_at,
                metadata,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now())
            ON CONFLICT (contact_id, channel_type, normalized_value) DO UPDATE SET
                channel_value = EXCLUDED.channel_value,
                is_primary = contact_channels.is_primary OR EXCLUDED.is_primary,
                verified_at = COALESCE(contact_channels.verified_at, EXCLUDED.verified_at),
                metadata = COALESCE(contact_channels.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb),
                updated_at = now()
            """,
            contact_id,
            channel_type,
            channel_value,
            normalized,
            is_primary,
            None,
            json.dumps(metadata),
        )
        if channel_type == "telegram_user_id":
            await conn.execute(
                """
                UPDATE contact_channels
                SET verified_at = COALESCE(verified_at, now()),
                    updated_at = now()
                WHERE contact_id = $1
                  AND channel_type = 'telegram_user_id'
                  AND normalized_value = $2
                """,
                contact_id,
                normalized,
            )


async def list_for_contact(state_path: str, contact_id: int) -> list[dict]:
    rows = await get_pool().fetch(
        """
        SELECT *
        FROM contact_channels
        WHERE contact_id = $1
        ORDER BY channel_type, is_primary DESC, updated_at DESC, id DESC
        """,
        contact_id,
    )
    return [_row_to_dict(row) for row in rows]


async def find_contact_ids_by_channel(
    state_path: str,
    *,
    channel_type: str,
    value: Any,
) -> list[int]:
    normalized = normalize_channel_value(channel_type, value)
    if normalized is None:
        return []
    rows = await get_pool().fetch(
        """
        SELECT DISTINCT contact_id
        FROM contact_channels
        WHERE channel_type = $1
          AND normalized_value = $2
        ORDER BY contact_id
        """,
        channel_type,
        normalized,
    )
    return [int(row["contact_id"]) for row in rows]
