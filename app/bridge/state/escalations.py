"""
Escalation state — persisted as JSON files.

Layout: {state_path}/escalations/{booking_id}.json

Escalation lifecycle:
  pending  → tutor notified, waiting for reply
  resolved → tutor replied, answer sent to student
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _esc_path(state_path: str, booking_id: str) -> Path:
    p = Path(state_path) / "escalations"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{booking_id}.json"


async def create(
    state_path: str,
    booking_id: str,
    question: str,
    tutor_message_id: int | None = None,
    reason: str | None = None,
) -> dict:
    data = {
        "booking_id": booking_id,
        "status": "pending",
        "reason": reason,
        "question": question,
        "tutor_message_id": tutor_message_id,
        "tutor_reply": None,
        "resolved_by": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
    }
    path = _esc_path(state_path, booking_id)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    await _export_to_obsidian(state_path, data)
    return data


async def load(state_path: str, booking_id: str) -> dict | None:
    path = _esc_path(state_path, booking_id)
    if not path.exists():
        return None
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return json.loads(text)


async def resolve(
    state_path: str, booking_id: str, tutor_reply: str, resolved_by: str | None = None
) -> dict | None:
    data = await load(state_path, booking_id)
    if data is None:
        return None
    data["status"] = "resolved"
    data["tutor_reply"] = tutor_reply
    data["resolved_by"] = resolved_by
    data["resolved_at"] = datetime.now(timezone.utc).isoformat()
    path = _esc_path(state_path, booking_id)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    await _export_to_obsidian(state_path, data)
    return data


async def find_pending_by_tutor_message(
    state_path: str, tutor_message_id: int
) -> dict | None:
    """Find pending escalation by the message ID sent to tutor."""

    def _scan() -> dict | None:
        esc_dir = Path(state_path) / "escalations"
        if not esc_dir.exists():
            return None
        for path in esc_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (
                    data.get("status") == "pending"
                    and data.get("tutor_message_id") == tutor_message_id
                ):
                    return data
            except Exception:
                continue
        return None

    return await asyncio.to_thread(_scan)


async def _export_to_obsidian(state_path: str, data: dict) -> None:
    try:
        from obsidian_adapter.writer import export_escalation
        knowledge_path = str(Path(state_path).parent / "knowledge")
        await export_escalation(knowledge_path, data)
    except Exception as exc:
        logger.warning("Failed to export escalation to Obsidian: %s", exc)
