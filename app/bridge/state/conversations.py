"""
Conversation history — persisted as JSON files.

Layout: {state_path}/conversations/{booking_id}.json
Each file is a list of message dicts: {role, content, ts}.

On every append, the conversation is also exported to Obsidian-compatible
markdown in the knowledge directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _conv_path(state_path: str, booking_id: str) -> Path:
    p = Path(state_path) / "conversations"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{booking_id}.json"


async def load(state_path: str, booking_id: str) -> list[dict]:
    path = _conv_path(state_path, booking_id)
    if not path.exists():
        return []
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return json.loads(text)


async def append(
    state_path: str,
    booking_id: str,
    role: str,
    content: str,
) -> None:
    """Append a single message turn to the conversation history."""
    history = await load(state_path, booking_id)
    history.append(
        {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    path = _conv_path(state_path, booking_id)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Export to Obsidian
    try:
        from obsidian_adapter.writer import export_conversation
        knowledge_path = str(Path(state_path).parent / "knowledge")
        await export_conversation(knowledge_path, booking_id, history)
    except Exception as exc:
        logger.warning("Failed to export conversation to Obsidian: %s", exc)
