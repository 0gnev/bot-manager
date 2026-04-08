"""
Conversation history — persisted in PostgreSQL.

Chat metadata lives on the ``contacts`` table when a contact exists, and falls
back to ``bookings`` for legacy/unlinked rows.
Messages are rows in the ``messages`` table keyed by ``contact_id`` with an
optional ``booking_id`` context.

On every append the conversation is also exported to Obsidian-compatible
markdown in the knowledge directory.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.db import get_pool
from bridge.state.chat import Chat, OperatingMode
from bridge.state import deliveries as delivery_state

logger = logging.getLogger(__name__)

# Columns on the bookings table that correspond to Chat metadata fields.
_CHAT_META_COLUMNS = frozenset({
    "mode",
    "status",
    "automation_enabled",
    "assigned_human",
    "escalation_state",
    "scenario_type",
    "current_stage",
    "confidence",
    "escalation_reason",
    "draft",
})


def _attachment_row_to_dict(row) -> dict:
    created_at = row["created_at"]
    return {
        "id": row["id"],
        "file_id": row["file_id"],
        "file_type": row["file_type"],
        "mime_type": row["mime_type"],
        "local_path": row["local_path"],
        "caption": row["caption"],
        "created_at": created_at.isoformat() if created_at else "",
    }


def _message_row_to_dict(row, attachments: list[dict] | None = None) -> dict[str, Any]:
    created_at = row["created_at"]
    model_output = row.get("model_output")
    if isinstance(model_output, str):
        try:
            model_output = json.loads(model_output)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "ts": created_at.isoformat() if created_at else "",
        "direction": row.get("direction"),
        "source": row.get("source"),
        "delivery_status": row.get("delivery_status"),
        "transport_chat_id": row.get("transport_chat_id"),
        "transport_message_id": row.get("transport_message_id"),
        "model_output": model_output,
        "attachments": attachments or [],
    }


async def load_chat(state_path: str, booking_id: str) -> Chat:
    """Load the full Chat object for a booking or contact-scoped conversation."""
    return await _load_chat_impl(state_path, booking_id=booking_id)


async def load_chat_by_contact(state_path: str, contact_id: int, booking_id: str | None = None) -> Chat:
    return await _load_chat_impl(state_path, booking_id=booking_id, contact_id=contact_id)


async def _load_chat_impl(
    state_path: str,
    booking_id: str | None = None,
    *,
    contact_id: int | None = None,
) -> Chat:
    pool = get_pool()
    async with pool.acquire() as conn:
        scope = await _resolve_scope(conn, booking_id=booking_id, contact_id=contact_id)
        meta_row = None
        if scope["contact_id"] is not None:
            meta_row = await conn.fetchrow(
                """
                SELECT mode, status, automation_enabled, assigned_human,
                       escalation_state, scenario_type, current_stage,
                       confidence, escalation_reason, draft,
                       created_at, updated_at
                FROM contacts WHERE id = $1
                """,
                scope["contact_id"],
            )
            msg_rows = await conn.fetch(
                """
                SELECT id, role, content, created_at, direction, source,
                       delivery_status, transport_chat_id, transport_message_id,
                       model_output
                FROM messages
                WHERE contact_id = $1
                ORDER BY created_at
                """,
                scope["contact_id"],
            )
        else:
            meta_row = await conn.fetchrow(
                """
                SELECT mode, status, automation_enabled, assigned_human,
                       escalation_state, scenario_type, current_stage,
                       confidence, escalation_reason, draft,
                       created_at, updated_at
                FROM bookings WHERE booking_id = $1
                """,
                scope["booking_id"],
            )
            msg_rows = await conn.fetch(
                """
                SELECT id, role, content, created_at, direction, source,
                       delivery_status, transport_chat_id, transport_message_id,
                       model_output
                FROM messages
                WHERE booking_id = $1
                ORDER BY created_at
                """,
                scope["booking_id"],
            )
        attachment_map: dict[int, list[dict]] = {}
        message_ids = [row["id"] for row in msg_rows]
        if message_ids:
            attachment_rows = await conn.fetch(
                """
                SELECT id, message_id, file_id, file_type, mime_type, local_path, caption, created_at
                FROM attachments
                WHERE message_id = ANY($1::bigint[])
                ORDER BY created_at
                """,
                message_ids,
            )
            for row in attachment_rows:
                attachment_map.setdefault(row["message_id"], []).append(_attachment_row_to_dict(row))

    messages = [_message_row_to_dict(r, attachment_map.get(r["id"], [])) for r in msg_rows]

    if meta_row is None:
        now = datetime.now(timezone.utc).isoformat()
        return Chat(
            booking_id=scope["booking_id"],
            contact_id=scope["contact_id"],
            messages=messages,
            created_at=now,
            updated_at=now,
        )

    draft = meta_row["draft"]
    if isinstance(draft, str):
        try:
            draft = json.loads(draft)
        except (json.JSONDecodeError, TypeError):
            pass

    return Chat(
        booking_id=scope["booking_id"],
        contact_id=scope["contact_id"],
        mode=OperatingMode(meta_row["mode"] or "auto"),
        status=meta_row["status"] or "active",
        automation_enabled=meta_row["automation_enabled"] if meta_row["automation_enabled"] is not None else True,
        assigned_human=meta_row["assigned_human"],
        escalation_state=meta_row["escalation_state"] or "none",
        scenario_type=meta_row["scenario_type"] or "general_support",
        current_stage=meta_row["current_stage"] or "new",
        confidence=meta_row["confidence"],
        escalation_reason=meta_row["escalation_reason"],
        messages=messages,
        draft=draft,
        created_at=meta_row["created_at"].isoformat() if meta_row["created_at"] else "",
        updated_at=meta_row["updated_at"].isoformat() if meta_row["updated_at"] else "",
    )


async def save_chat(state_path: str, chat: Chat) -> None:
    """Persist chat metadata columns back to the contact or booking scope.

    Messages are NOT re-inserted — they are append-only via ``append()``.
    """
    pool = get_pool()
    draft_json = json.dumps(chat.draft) if chat.draft else None
    if chat.contact_id is not None:
        await pool.execute(
            """
            UPDATE contacts
            SET mode = $2,
                status = $3,
                automation_enabled = $4,
                assigned_human = $5,
                escalation_state = $6,
                scenario_type = $7,
                current_stage = $8,
                confidence = $9,
                escalation_reason = $10,
                draft = $11::jsonb,
                updated_at = now()
            WHERE id = $1
            """,
            chat.contact_id,
            chat.mode.value,
            chat.status,
            chat.automation_enabled,
            chat.assigned_human,
            chat.escalation_state,
            chat.scenario_type,
            chat.current_stage,
            chat.confidence,
            chat.escalation_reason,
            draft_json,
        )
        return

    await pool.execute(
        """
        UPDATE bookings
        SET mode = $2,
            status = $3,
            automation_enabled = $4,
            assigned_human = $5,
            escalation_state = $6,
            scenario_type = $7,
            current_stage = $8,
            confidence = $9,
            escalation_reason = $10,
            draft = $11::jsonb,
            updated_at = now()
        WHERE booking_id = $1
        """,
        chat.booking_id,
        chat.mode.value,
        chat.status,
        chat.automation_enabled,
        chat.assigned_human,
        chat.escalation_state,
        chat.scenario_type,
        chat.current_stage,
        chat.confidence,
        chat.escalation_reason,
        draft_json,
    )


async def update_metadata(
    state_path: str,
    booking_id: str | None = None,
    *,
    contact_id: int | None = None,
    **fields,
) -> Chat:
    """Update one or more chat metadata fields on the active conversation scope."""
    valid = {k: v for k, v in fields.items() if k in _CHAT_META_COLUMNS}
    if valid:
        pool = get_pool()
        async with pool.acquire() as conn:
            scope = await _resolve_scope(conn, booking_id=booking_id, contact_id=contact_id)
            target_id = scope["contact_id"] if scope["contact_id"] is not None else scope["booking_id"]
            target_table = "contacts" if scope["contact_id"] is not None else "bookings"
            set_parts = []
            params = []
            idx = 2
            for col, val in valid.items():
                if col == "draft":
                    val = json.dumps(val) if val else None
                    set_parts.append(f"{col} = ${idx}::jsonb")
                elif col == "mode" and isinstance(val, OperatingMode):
                    val = val.value
                    set_parts.append(f"{col} = ${idx}")
                else:
                    set_parts.append(f"{col} = ${idx}")
                params.append(val)
                idx += 1
            set_parts.append("updated_at = now()")
            query = f"UPDATE {target_table} SET {', '.join(set_parts)} WHERE {'id' if target_table == 'contacts' else 'booking_id'} = $1"
            await conn.execute(query, target_id, *params)

    if contact_id is not None:
        return await load_chat_by_contact(state_path, contact_id, booking_id=booking_id)
    return await load_chat(state_path, booking_id)


async def load(state_path: str, booking_id: str | None = None, *, contact_id: int | None = None) -> list[dict]:
    """Load just the messages list (backward-compatible convenience)."""
    chat = await _load_chat_impl(state_path, booking_id=booking_id, contact_id=contact_id)
    return chat.messages


async def append(
    state_path: str,
    role: str,
    content: str,
    booking_id: str | None = None,
    *,
    contact_id: int | None = None,
    direction: str | None = None,
    source: str | None = None,
    delivery_status: str | None = None,
    transport: str | None = None,
    transport_chat_id: int | None = None,
    transport_message_id: int | None = None,
    delivery_attempts: int | None = None,
    delivery_recipient: str | None = None,
    delivery_error_text: str | None = None,
    delivery_payload: dict | None = None,
    model_output: dict | None = None,
    attachments: list[dict] | None = None,
) -> dict[str, Any]:
    """Append a single message turn to the conversation history."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            scope = await _resolve_scope(conn, booking_id=booking_id, contact_id=contact_id)
            resolved_direction = direction or _default_direction(role)
            resolved_delivery_status = delivery_status or _default_delivery_status(resolved_direction)
            resolved_transport = transport or _resolve_transport(
                source,
                transport_chat_id=transport_chat_id,
                transport_message_id=transport_message_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO messages (
                    contact_id,
                    booking_id,
                    role,
                    content,
                    direction,
                    source,
                    delivery_status,
                    transport_chat_id,
                    transport_message_id,
                    model_output
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                RETURNING id, role, content, created_at, direction, source,
                          delivery_status, transport_chat_id, transport_message_id,
                          model_output
                """,
                scope["contact_id"],
                scope["booking_id"],
                role,
                content,
                resolved_direction,
                source,
                resolved_delivery_status,
                transport_chat_id,
                transport_message_id,
                json.dumps(model_output) if model_output is not None else None,
            )
            created_attachments: list[dict] = []
            for attachment in attachments or []:
                attachment_row = await conn.fetchrow(
                    """
                    INSERT INTO attachments (
                        message_id,
                        contact_id,
                        booking_id,
                        file_id,
                        file_type,
                        mime_type,
                        local_path,
                        caption
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id, message_id, file_id, file_type, mime_type, local_path, caption, created_at
                    """,
                    row["id"],
                    scope["contact_id"],
                    scope["booking_id"],
                    attachment.get("file_id"),
                    attachment.get("file_type", "file"),
                    attachment.get("mime_type"),
                    attachment.get("local_path"),
                    attachment.get("caption"),
                )
                created_attachments.append(_attachment_row_to_dict(attachment_row))
            if resolved_transport is not None:
                await delivery_state.record_conn(
                    conn,
                    message_id=row["id"],
                    contact_id=scope["contact_id"],
                    booking_id=scope["booking_id"],
                    direction=resolved_direction,
                    transport=resolved_transport,
                    source=source,
                    status=resolved_delivery_status,
                    chat_id=transport_chat_id,
                    transport_message_id=transport_message_id,
                    attempts=max(int(delivery_attempts or 1), 1),
                    recipient=delivery_recipient,
                    error_text=delivery_error_text,
                    payload=delivery_payload,
                )
            if scope["contact_id"] is not None:
                await conn.execute(
                    "UPDATE contacts SET updated_at = now() WHERE id = $1",
                    scope["contact_id"],
                )
            elif scope["booking_id"] is not None:
                await conn.execute(
                    "UPDATE bookings SET updated_at = now() WHERE booking_id = $1",
                    scope["booking_id"],
                )

    # Export to Obsidian
    try:
        from obsidian_adapter.writer import export_conversation
        from bridge.state import bookings, contacts

        knowledge_path = str(Path(state_path).parent / "knowledge")
        chat = await _load_chat_impl(state_path, booking_id=booking_id, contact_id=contact_id)
        booking_ctx = await bookings.load(state_path, chat.booking_id) if chat.booking_id else None
        contact_ctx = (
            await contacts.load(state_path, chat.contact_id)
            if chat.contact_id is not None else None
        )
        await export_conversation(
            knowledge_path,
            chat.messages,
            booking_id=chat.booking_id,
            contact_id=chat.contact_id,
            booking=booking_ctx,
            contact=contact_ctx,
        )
    except Exception as exc:
        logger.warning("Failed to export conversation to Obsidian: %s", exc)

    return _message_row_to_dict(row, created_attachments)


async def _resolve_scope(conn, *, booking_id: str | None, contact_id: int | None) -> dict:
    resolved_contact_id = contact_id
    resolved_booking_id = booking_id

    if resolved_contact_id is None and resolved_booking_id is not None:
        row = await conn.fetchrow(
            "SELECT contact_id FROM bookings WHERE booking_id = $1",
            resolved_booking_id,
        )
        if row is not None:
            resolved_contact_id = row["contact_id"]

    if resolved_contact_id is None and resolved_booking_id is None:
        raise ValueError("contact_id or booking_id is required")

    return {
        "contact_id": resolved_contact_id,
        "booking_id": resolved_booking_id,
    }


def _default_direction(role: str) -> str:
    if role == "user":
        return "inbound"
    if role == "assistant":
        return "outbound"
    return "internal"


def _default_delivery_status(direction: str) -> str:
    if direction == "inbound":
        return "received"
    if direction == "outbound":
        return "sent"
    return "recorded"


def _resolve_transport(
    source: str | None,
    *,
    transport_chat_id: int | None,
    transport_message_id: int | None,
) -> str | None:
    if source and "telegram" in source:
        return "telegram"
    if transport_chat_id is not None or transport_message_id is not None:
        return "telegram"
    return None
