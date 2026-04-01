"""
Approval state — persisted as JSON files.

Layout: {state_path}/approvals/{approval_id}.json

Approval lifecycle:
  pending  -> AI draft sent to tutor for review
  approved -> tutor approved, draft sent to student
  rejected -> tutor rejected, draft discarded
  edited   -> tutor edited and approved, edited version sent to student
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _approvals_dir(state_path: str) -> Path:
    p = Path(state_path) / "approvals"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _approval_path(state_path: str, approval_id: str) -> Path:
    return _approvals_dir(state_path) / f"{approval_id}.json"


async def _write(path: Path, data: dict) -> None:
    await asyncio.to_thread(
        path.write_text,
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def create_approval(
    state_path: str,
    booking_id: str,
    student_chat_id: int,
    draft_content: str,
    action: str,
    confidence: float,
) -> dict:
    approval_id = uuid.uuid4().hex[:12]
    data = {
        "approval_id": approval_id,
        "booking_id": booking_id,
        "student_chat_id": student_chat_id,
        "draft_content": draft_content,
        "action": action,
        "confidence": confidence,
        "status": "pending",
        "tutor_message_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
    }
    await _write(_approval_path(state_path, approval_id), data)
    logger.info("Created approval %s for booking %s", approval_id, booking_id)
    return data


async def get_approval(state_path: str, approval_id: str) -> dict | None:
    path = _approval_path(state_path, approval_id)
    if not path.exists():
        return None
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return json.loads(text)


async def resolve_approval(
    state_path: str,
    approval_id: str,
    status: str,
    final_content: str | None = None,
) -> dict | None:
    data = await get_approval(state_path, approval_id)
    if data is None:
        return None
    data["status"] = status
    if final_content is not None:
        data["draft_content"] = final_content
    data["resolved_at"] = datetime.now(timezone.utc).isoformat()
    await _write(_approval_path(state_path, approval_id), data)
    logger.info("Resolved approval %s -> %s", approval_id, status)
    return data


async def set_tutor_message_id(
    state_path: str, approval_id: str, message_id: int
) -> None:
    data = await get_approval(state_path, approval_id)
    if data is None:
        return
    data["tutor_message_id"] = message_id
    await _write(_approval_path(state_path, approval_id), data)


async def list_pending(
    state_path: str, booking_id: str | None = None
) -> list[dict]:
    def _scan() -> list[dict]:
        approvals_dir = _approvals_dir(state_path)
        results = []
        for path in sorted(approvals_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") != "pending":
                    continue
                if booking_id and data.get("booking_id") != booking_id:
                    continue
                results.append(data)
            except Exception:
                continue
        return results

    return await asyncio.to_thread(_scan)


async def find_pending_by_tutor_message(
    state_path: str, tutor_message_id: int
) -> dict | None:
    def _scan() -> dict | None:
        approvals_dir = _approvals_dir(state_path)
        for path in approvals_dir.glob("*.json"):
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
