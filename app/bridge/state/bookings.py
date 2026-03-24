"""
Booking state — persisted as JSON files.

Layout: {state_path}/bookings/{booking_id}.json

On every save, the booking is also exported to Obsidian-compatible markdown
in the knowledge directory (parallel to state_path).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _bookings_dir(state_path: str) -> Path:
    p = Path(state_path) / "bookings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _booking_path(state_path: str, booking_id: str) -> Path:
    return _bookings_dir(state_path) / f"{booking_id}.json"


def _knowledge_path(state_path: str) -> str:
    """Derive knowledge path from state path (sibling directory)."""
    return str(Path(state_path).parent / "knowledge")


async def save(state_path: str, booking_id: str, data: dict) -> None:
    data = {**data, "updated_at": datetime.now(timezone.utc).isoformat()}
    path = _booking_path(state_path, booking_id)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    # Export to Obsidian
    try:
        from obsidian_adapter.writer import export_booking
        await export_booking(_knowledge_path(state_path), data)
    except Exception as exc:
        logger.warning("Failed to export booking to Obsidian: %s", exc)


async def load(state_path: str, booking_id: str) -> dict | None:
    path = _booking_path(state_path, booking_id)
    if not path.exists():
        return None
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return json.loads(text)


async def link_telegram_user(
    state_path: str, booking_id: str, telegram_user_id: int
) -> dict | None:
    """Attach telegram_user_id to an existing booking. Returns updated booking or None."""
    booking = await load(state_path, booking_id)
    if booking is None:
        return None
    booking["telegram_user_id"] = telegram_user_id
    await save(state_path, booking_id, booking)
    return booking


async def find_by_telegram_user(
    state_path: str, telegram_user_id: int
) -> dict | None:
    """Scan bookings dir to find the active booking for a Telegram user."""

    def _scan() -> dict | None:
        for path in _bookings_dir(state_path).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("telegram_user_id") == telegram_user_id:
                    return data
            except Exception:
                continue
        return None

    return await asyncio.to_thread(_scan)


async def find_by_telegram_username(
    state_path: str, username: str
) -> dict | None:
    """Find an active, unlinked booking where attendee.telegram matches @username."""
    normalized = username.lower().lstrip("@")

    def _scan() -> dict | None:
        for path in _bookings_dir(state_path).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") != "active":
                    continue
                if data.get("telegram_user_id") is not None:
                    continue
                attendee = data.get("attendee") or {}
                tg = (attendee.get("telegram") or "").lower().lstrip("@")
                if tg and tg == normalized:
                    return data
            except Exception:
                continue
        return None

    return await asyncio.to_thread(_scan)
