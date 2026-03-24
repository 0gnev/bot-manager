"""
Tutor-side handlers.

The tutor replies to an escalation by replying to the forwarded message.
Bridge detects the reply-to message ID, looks up the escalation, and routes
the answer back to the student via the student bot.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from bridge.bot import registry
from bridge.config import Settings
from bridge.state import bookings, escalations
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="tutor")


@router.message(F.text, F.reply_to_message)
async def on_tutor_reply(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    replied_to_id = message.reply_to_message.message_id
    escalation = await escalations.find_pending_by_tutor_message(
        settings.state_path, replied_to_id
    )
    if not escalation:
        return  # not an escalation reply

    booking_id = escalation["booking_id"]
    booking = await bookings.load(settings.state_path, booking_id)
    if not booking:
        logger.warning("Tutor replied for missing booking: %s", booking_id)
        return

    student_id = booking.get("telegram_user_id")
    if not student_id:
        await message.answer("Студент ещё не подключился через deeplink.")
        return

    student_bot = registry.get_student()
    if student_bot is None:
        logger.error("Student bot not available")
        await message.answer("Ошибка: не удалось отправить ответ студенту.")
        return

    await student_bot.send_message(student_id, message.text)
    await escalations.resolve(settings.state_path, booking_id, message.text)
    await message.answer(templates.tutor_answer_sent())
    logger.info("Tutor reply routed: booking=%s → student=%s", booking_id, student_id)
