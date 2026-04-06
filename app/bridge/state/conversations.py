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

from bridge.db import get_pool
from bridge.state.chat import Chat, OperatingMode

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
                SELECT role, content, created_at
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
                SELECT role, content, created_at
                FROM messages
                WHERE booking_id = $1
                ORDER BY created_at
                """,
                scope["booking_id"],
            )

    messages = [
        {"role": r["role"], "content": r["content"], "ts": r["created_at"].isoformat()}
        for r in msg_rows
    ]

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
) -> None:
    """Append a single message turn to the conversation history."""
    pool = get_pool()
    async with pool.acquire() as conn:
        scope = await _resolve_scope(conn, booking_id=booking_id, contact_id=contact_id)
        await conn.execute(
            """
            INSERT INTO messages (contact_id, booking_id, role, content)
            VALUES ($1, $2, $3, $4)
            """,
            scope["contact_id"],
            scope["booking_id"],
            role,
            content,
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

        knowledge_path = str(Path(state_path).parent / "knowledge")
        chat = await _load_chat_impl(state_path, booking_id=booking_id, contact_id=contact_id)
        export_id = chat.booking_id or (f"contact-{chat.contact_id}" if chat.contact_id is not None else "conversation")
        await export_conversation(knowledge_path, export_id, chat.messages)
    except Exception as exc:
        logger.warning("Failed to export conversation to Obsidian: %s", exc)


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
