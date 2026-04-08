"""
/start handler — links student's Telegram account to their booking.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bridge.audit import audit_log
from bridge.bot.filters import StudentBotFilter
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.state import bookings, contacts, conversations
from bridge.timezones import parse_datetime
from telegram_adapter.deeplink import parse_start_payload
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(StudentBotFilter(), CommandStart())
async def cmd_start(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    user_id = str(message.from_user.id)
    await audit_log("bot", "start_command", actor=user_id)

    args = message.text.split(maxsplit=1)[1] if " " in (message.text or "") else ""

    booking: dict | None = None

    if args:
        booking_id = parse_start_payload(args)
        booking = await bookings.load(settings.state_path, booking_id)
        if booking is None:
            logger.warning("Unknown booking_id from deeplink: %s", booking_id)
            await message.answer(templates.booking_not_found())
            return
        if not await _can_link_booking_for_student(message, settings, booking):
            logger.warning(
                "Rejected booking link attempt: booking=%s user_id=%s username=%s",
                booking_id,
                message.from_user.id,
                message.from_user.username,
            )
            await message.answer(templates.booking_not_found())
            return
    else:
        booking = await bookings.find_by_telegram_user(
            settings.state_path, message.from_user.id
        )
        if booking is None:
            linked_bookings = await bookings.find_all_by_telegram_user(
                settings.state_path, message.from_user.id
            )
            if linked_bookings:
                await message.answer(_multiple_bookings_notice(linked_bookings))
                return
        if booking is None:
            username = _normalized_username(message.from_user.username)
            if username:
                candidates = [
                    item for item in await contacts.find_all_with_active_bookings_by_telegram_username(
                        settings.state_path,
                        username,
                    )
                    if item.get("telegram_user_id") in (None, message.from_user.id)
                ]
                if len(candidates) > 1:
                    await message.answer(templates.booking_link_ambiguous())
                    return
                if len(candidates) == 1:
                    contact = await contacts.attach_telegram_identity(
                        settings.state_path,
                        candidates[0]["id"],
                        telegram_user_id=message.from_user.id,
                        telegram_username=message.from_user.username,
                        name=message.from_user.full_name,
                    )
                    if contact is not None:
                        booking = await bookings.resolve_context_for_contact(
                            settings.state_path,
                            contact["id"],
                        )
        if booking is None:
            await contacts.ensure_telegram_contact(
                settings.state_path,
                message.from_user.id,
                telegram_username=message.from_user.username,
                name=message.from_user.full_name,
            )
            await message.answer(templates.contact_only_welcome())
            return

    booking_id = booking["booking_id"]

    if booking.get("telegram_user_id") not in (None, message.from_user.id):
        await message.answer(templates.booking_linked_elsewhere())
        return

    if booking.get("telegram_user_id") == message.from_user.id:
        booking = await bookings.link_telegram_user(
            settings.state_path, booking_id, message.from_user.id
        ) or booking
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
    contact = (
        await contacts.load(settings.state_path, booking["contact_id"])
        if booking.get("contact_id") is not None
        else None
    )
    student_time_zone = attendee.get("timeZone") or (contact or {}).get("time_zone")

    await send_student_message(
        bot=message.bot,
        chat_id=message.chat.id,
        text=templates.welcome(
            student_name,
            event_title,
            start_time,
            time_zone_name=student_time_zone,
        ),
        booking_id=booking_id,
        contact_id=booking.get("contact_id"),
        settings=settings,
        source="start_welcome",
        actor="system",
    )
    await send_student_message(
        bot=message.bot,
        chat_id=message.chat.id,
        text=templates.session_details(
            event_title,
            start_time,
            end_time,
            meeting_url,
            time_zone_name=student_time_zone,
        ),
        booking_id=booking_id,
        contact_id=booking.get("contact_id"),
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
    return parse_datetime(value)


def _normalized_username(value: str | None) -> str | None:
    normalized = (value or "").lstrip("@").strip().lower()
    return normalized or None


async def _can_link_booking_for_student(
    message: Message,
    settings: Settings,
    booking: dict,
) -> bool:
    user_id = message.from_user.id
    if booking.get("telegram_user_id") == user_id:
        return True
    if booking.get("telegram_user_id") not in (None, user_id):
        return False

    contact = (
        await contacts.load(settings.state_path, booking["contact_id"])
        if booking.get("contact_id") is not None
        else None
    )
    contact_user_id = (contact or {}).get("telegram_user_id")
    if contact_user_id == user_id:
        return True
    if contact_user_id not in (None, user_id):
        return False

    recorded_username = _normalized_username(
        (contact or {}).get("telegram_username")
        or ((booking.get("attendee") or {}).get("telegram"))
    )
    return recorded_username is not None and recorded_username == _normalized_username(
        message.from_user.username
    )


def _multiple_bookings_notice(bookings_list: list[dict]) -> str:
    prepared = []
    for booking in bookings_list:
        attendee = booking.get("attendee") or {}
        prepared.append(
            {
                **booking,
                "start_time_label": templates.fmt_dt(
                    _parse_dt(booking.get("start_time")),
                    time_zone_name=attendee.get("timeZone"),
                    include_time_zone=True,
                ),
            }
        )
    return templates.multiple_bookings_found(prepared)
