"""
Conversation history — persisted in PostgreSQL.

Chat metadata lives on the ``bookings`` table.
Messages are rows in the ``messages`` table.
The ``Chat`` dataclass is a view-model reconstructed from both.

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
    """Load the full Chat object for a booking (metadata from bookings + messages)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        brow = await conn.fetchrow(
            """
            SELECT mode, status, automation_enabled, assigned_human,
                   escalation_state, scenario_type, current_stage,
                   confidence, escalation_reason, draft,
                   created_at, updated_at
            FROM bookings WHERE booking_id = $1
            """,
            booking_id,
        )

        msg_rows = await conn.fetch(
            "SELECT role, content, created_at FROM messages WHERE booking_id = $1 ORDER BY created_at",
            booking_id,
        )

    messages = [
        {"role": r["role"], "content": r["content"], "ts": r["created_at"].isoformat()}
        for r in msg_rows
    ]

    if brow is None:
        now = datetime.now(timezone.utc).isoformat()
        return Chat(booking_id=booking_id, messages=messages, created_at=now, updated_at=now)

    draft = brow["draft"]
    if isinstance(draft, str):
        try:
            draft = json.loads(draft)
        except (json.JSONDecodeError, TypeError):
            pass

    return Chat(
        booking_id=booking_id,
        mode=OperatingMode(brow["mode"] or "auto"),
        status=brow["status"] or "active",
        automation_enabled=brow["automation_enabled"] if brow["automation_enabled"] is not None else True,
        assigned_human=brow["assigned_human"],
        escalation_state=brow["escalation_state"] or "none",
        scenario_type=brow["scenario_type"] or "general_support",
        current_stage=brow["current_stage"] or "new",
        confidence=brow["confidence"],
        escalation_reason=brow["escalation_reason"],
        messages=messages,
        draft=draft,
        created_at=brow["created_at"].isoformat() if brow["created_at"] else "",
        updated_at=brow["updated_at"].isoformat() if brow["updated_at"] else "",
    )


async def save_chat(state_path: str, chat: Chat) -> None:
    """Persist chat metadata columns back to the bookings table.

    Messages are NOT re-inserted — they are append-only via ``append()``.
    """
    pool = get_pool()
    draft_json = json.dumps(chat.draft) if chat.draft else None
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
    booking_id: str,
    **fields,
) -> Chat:
    """Update one or more chat metadata fields on the bookings table."""
    valid = {k: v for k, v in fields.items() if k in _CHAT_META_COLUMNS}
    if valid:
        pool = get_pool()
        set_parts = []
        params = []
        idx = 2  # $1 is booking_id
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
        query = f"UPDATE bookings SET {', '.join(set_parts)} WHERE booking_id = $1"
        await pool.execute(query, booking_id, *params)

    return await load_chat(state_path, booking_id)


async def load(state_path: str, booking_id: str) -> list[dict]:
    """Load just the messages list (backward-compatible convenience)."""
    chat = await load_chat(state_path, booking_id)
    return chat.messages


async def append(
    state_path: str,
    booking_id: str,
    role: str,
    content: str,
) -> None:
    """Append a single message turn to the conversation history."""
    pool = get_pool()
    await pool.execute(
        "INSERT INTO messages (booking_id, role, content) VALUES ($1, $2, $3)",
        booking_id,
        role,
        content,
    )
    await pool.execute(
        "UPDATE bookings SET updated_at = now() WHERE booking_id = $1",
        booking_id,
    )

    # Export to Obsidian
    try:
        from obsidian_adapter.writer import export_conversation

        knowledge_path = str(Path(state_path).parent / "knowledge")
        chat = await load_chat(state_path, booking_id)
        await export_conversation(knowledge_path, booking_id, chat.messages)
    except Exception as exc:
        logger.warning("Failed to export conversation to Obsidian: %s", exc)
