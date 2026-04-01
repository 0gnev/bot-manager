"""
/start handler — links student's Telegram account to their booking.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bridge.audit import audit_log
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.state import bookings, conversations
from telegram_adapter.deeplink import parse_start_payload
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    user_id = str(message.from_user.id)
    await audit_log("bot", "start_command", actor=user_id)

    args = message.text.split(maxsplit=1)[1] if " " in (message.text or "") else ""

    booking: dict | None = None

    if args:
        # Deeplink with booking ID
        booking_id = parse_start_payload(args)
        booking = await bookings.load(settings.state_path, booking_id)
        if booking is None:
            logger.warning("Unknown booking_id from deeplink: %s", booking_id)
            await message.answer(templates.booking_not_found())
            return
    else:
        # No deeplink — try matching by Telegram username
        username = message.from_user.username
        if username:
            booking = await bookings.find_by_telegram_username(
                settings.state_path, username
            )
        if booking is None:
            # Also check if already linked by user ID
            booking = await bookings.find_by_telegram_user(
                settings.state_path, message.from_user.id
            )
        if booking is None:
            await message.answer(
                "Привет! Не нашёл вашу запись. "
                "Убедитесь, что при бронировании указан ваш Telegram."
            )
            return

    booking_id = booking["booking_id"]

    if booking.get("telegram_user_id") == message.from_user.id:
        await message.answer(templates.already_linked())
        return

    booking = await bookings.link_telegram_user(
        settings.state_path, booking_id, message.from_user.id
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id,
        current_stage="student_linked",
        status="active",
        automation_enabled=True,
    )

    start_time = _parse_dt(booking.get("start_time"))
    end_time = _parse_dt(booking.get("end_time"))
    event_title = booking.get("title", "Занятие")
    meeting_url = booking.get("meeting_url")

    attendee = booking.get("attendee") or {}
    student_name = attendee.get("name", "")

    await send_student_message(
        bot=message.bot,
        chat_id=message.chat.id,
        text=templates.welcome(student_name, event_title, start_time),
        booking_id=booking_id,
        settings=settings,
        source="start_welcome",
        actor="system",
    )
    await send_student_message(
        bot=message.bot,
        chat_id=message.chat.id,
        text=templates.session_details(event_title, start_time, end_time, meeting_url),
        booking_id=booking_id,
        settings=settings,
        source="start_session_details",
        actor="system",
        disable_web_page_preview=True,
    )
    logger.info("Student linked: user_id=%s → booking=%s", message.from_user.id, booking_id)
    await audit_log(
        "bot", "booking_linked",
        booking_id=booking_id,
        actor=user_id,
    )


def _parse_dt(value: str | None):
    if not value:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
