"""
/start handler — links student's Telegram account to their booking.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bridge.config import Settings
from bridge.state import bookings
from telegram_adapter.deeplink import parse_start_payload
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    args = message.text.split(maxsplit=1)[1] if " " in (message.text or "") else ""

    if not args:
        await message.answer(
            "Привет! Перейдите по ссылке из подтверждения бронирования, "
            "чтобы я мог найти вашу запись."
        )
        return

    booking_id = parse_start_payload(args)
    booking = await bookings.load(settings.state_path, booking_id)

    if booking is None:
        logger.warning("Unknown booking_id from deeplink: %s", booking_id)
        await message.answer(templates.booking_not_found())
        return

    if booking.get("telegram_user_id") == message.from_user.id:
        await message.answer(templates.already_linked())
        return

    booking = await bookings.link_telegram_user(
        settings.state_path, booking_id, message.from_user.id
    )

    from datetime import datetime
    start_time = _parse_dt(booking.get("start_time"))
    end_time = _parse_dt(booking.get("end_time"))
    event_title = booking.get("title", "Занятие")
    meeting_url = booking.get("meeting_url")

    attendee = booking.get("attendee") or {}
    student_name = attendee.get("name", "")

    await message.answer(templates.welcome(student_name, event_title, start_time))
    await message.answer(
        templates.session_details(event_title, start_time, end_time, meeting_url),
        disable_web_page_preview=True,
    )
    logger.info("Student linked: user_id=%s → booking=%s", message.from_user.id, booking_id)


def _parse_dt(value: str | None):
    if not value:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
