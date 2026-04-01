"""
Structured audit logger.

Writes JSON-lines to data/audit/audit.jsonl.
Auto-rotates when the file exceeds 10 MB.
Non-blocking: never raises exceptions to callers.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import aiofiles.os

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(os.environ.get("AUDIT_PATH", os.environ.get("audit_path", "/workspace/data/audit")))
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


async def audit_log(
    event_type: str,
    action: str,
    *,
    booking_id: str | None = None,
    actor: str | None = None,
    detail: dict | None = None,
    outcome: str = "success",
) -> None:
    """Append one audit entry. Never raises."""
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "booking_id": booking_id,
            "actor": actor,
            "action": action,
            "detail": detail or {},
            "outcome": outcome,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"

        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        path = _AUDIT_DIR / "audit.jsonl"

        await _maybe_rotate(path)

        async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
            await f.write(line)
    except Exception:
        # Log to stderr but never propagate
        logger.error("audit_log failed", exc_info=True)


async def _maybe_rotate(path: Path) -> None:
    """Rename current file if it exceeds the size limit."""
    try:
        if not path.exists():
            return
        size = (await aiofiles.os.stat(path)).st_size
        if size < _MAX_BYTES:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        rotated = path.with_name(f"audit-{stamp}.jsonl")
        await aiofiles.os.rename(path, rotated)
    except Exception:
        logger.error("audit rotation failed", exc_info=True)


async def read_recent(
    *,
    booking_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Read recent audit entries with optional filters (newest first)."""
    path = _AUDIT_DIR / "audit.jsonl"
    if not path.exists():
        return []

    entries: list[dict] = []
    try:
        async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if booking_id and entry.get("booking_id") != booking_id:
                    continue
                if event_type and entry.get("event_type") != event_type:
                    continue
                entries.append(entry)
    except Exception:
        logger.error("read_recent failed", exc_info=True)

    # Return newest first, capped at limit
    return entries[-limit:][::-1]
