"""
Escalation lifecycle.

1. Send escalation notice to tutor via tutor bot.
2. Save escalation state (pending).
3. Tell student their question was forwarded.

Tutor reply routing is handled in bot/handlers/tutor.py.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram.types import Message

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.config import Settings
from bridge.state import escalations
from telegram_adapter import templates

logger = logging.getLogger(__name__)


async def escalate(
    message: Message,
    booking: dict,
    question: str,
    settings: Settings,
    image_path: str | None = None,
) -> None:
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if tutor_chat_id:
        try:
            tutor_chat_id = int(tutor_chat_id)
        except (ValueError, TypeError):
            tutor_chat_id = None

    if not tutor_chat_id:
        logger.warning("TUTOR_CHAT_ID not configured; cannot escalate")
        await message.answer(
            "Не могу связаться с преподавателем прямо сейчас. Попробуйте позже."
        )
        return

    owner_bot = registry.get_owner()
    if owner_bot is None:
        logger.error("Owner bot not initialised")
        return

    booking_id = booking["booking_id"]
    attendee = booking.get("attendee") or {}

    notice = templates.escalation_notice(
        student_name=attendee.get("name", "Студент"),
        booking_id=booking_id,
        question=question,
        event_title=booking.get("title", "Занятие"),
        start_time=_parse_dt(booking.get("start_time")),
    )

    sent = await owner_bot.send_message(tutor_chat_id, notice)

    if image_path:
        try:
            with open(image_path, "rb") as f:
                await owner_bot.send_photo(
                    tutor_chat_id, f, reply_to_message_id=sent.message_id
                )
        except Exception as exc:
            logger.warning("Could not forward image to tutor: %s", exc)

    await escalations.create(
        settings.state_path,
        booking_id,
        question=question,
        tutor_message_id=sent.message_id,
    )

    await message.answer(templates.escalated_to_tutor())
    logger.info("Escalated booking=%s → tutor chat=%s", booking_id, tutor_chat_id)
    await audit_log(
        "escalation", "created",
        booking_id=booking_id,
        actor=str(message.from_user.id),
        detail={"has_image": image_path is not None},
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
