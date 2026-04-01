"""
Conversation history — persisted as JSON files.

Layout: {state_path}/conversations/{booking_id}.json
Each file stores a Chat object: {booking_id, mode, messages, draft, created_at, updated_at}.
Legacy files (plain list of messages) are auto-migrated on load.

On every append, the conversation is also exported to Obsidian-compatible
markdown in the knowledge directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bridge.state.chat import Chat, OperatingMode

logger = logging.getLogger(__name__)


def _conv_path(state_path: str, booking_id: str) -> Path:
    p = Path(state_path) / "conversations"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{booking_id}.json"


async def load_chat(state_path: str, booking_id: str) -> Chat:
    """Load the full Chat object for a booking."""
    path = _conv_path(state_path, booking_id)
    if not path.exists():
        now = datetime.now(timezone.utc).isoformat()
        return Chat(booking_id=booking_id, created_at=now, updated_at=now)
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    data = json.loads(text)
    # Backward compatibility: legacy files are plain lists of messages
    if isinstance(data, list):
        now = datetime.now(timezone.utc).isoformat()
        return Chat(booking_id=booking_id, messages=data, created_at=now, updated_at=now)
    return Chat.from_dict(data)


async def save_chat(state_path: str, chat: Chat) -> None:
    """Persist the full Chat object."""
    chat.updated_at = datetime.now(timezone.utc).isoformat()
    path = _conv_path(state_path, chat.booking_id)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(chat.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    chat = await load_chat(state_path, booking_id)
    chat.messages.append(
        {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    await save_chat(state_path, chat)
    # Export to Obsidian
    try:
        from obsidian_adapter.writer import export_conversation
        knowledge_path = str(Path(state_path).parent / "knowledge")
        await export_conversation(knowledge_path, booking_id, chat.messages)
    except Exception as exc:
        logger.warning("Failed to export conversation to Obsidian: %s", exc)
