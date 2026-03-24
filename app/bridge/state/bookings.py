"""
Booking state — persisted as JSON files.

Layout: {state_path}/bookings/{booking_id}.json
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


def _bookings_dir(state_path: str) -> Path:
    p = Path(state_path) / "bookings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _booking_path(state_path: str, booking_id: str) -> Path:
    return _bookings_dir(state_path) / f"{booking_id}.json"


async def save(state_path: str, booking_id: str, data: dict) -> None:
    data = {**data, "updated_at": datetime.now(timezone.utc).isoformat()}
    path = _booking_path(state_path, booking_id)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


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
