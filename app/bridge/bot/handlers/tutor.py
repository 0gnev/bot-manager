"""
Tutor-side handlers.

The tutor replies to an escalation by replying to the forwarded message.
Bridge detects the reply-to message ID, looks up the escalation, and routes
the answer back to the student via the student bot.

Additional features:
  - /mode {booking_id} auto|semi-auto|manual — switch operating mode
  - Approval inline-button callbacks (approve/reject)
  - Reply-to approval message = edit & approve
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bridge.approvals import handler as approval_handler
from bridge.audit import audit_log
from bridge.bot import registry
from bridge.clients.openclaw import OpenclawClient
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.state import approvals, bookings, conversations, escalations, OperatingMode
from obsidian_adapter.reader import search as knowledge_search
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="tutor")

_BOOKING_ID_RE = re.compile(r"ID брони:\s*(?:<code>)?([A-Za-z0-9_-]+)")


# -- /mode command -------------------------------------------------------------

@router.message(Command("mode"))
async def on_mode(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/mode {booking_id} auto|semi-auto|manual</code>"
        )
        return

    booking_id = parts[1]
    mode_str = parts[2].strip().lower()

    try:
        new_mode = OperatingMode(mode_str)
    except ValueError:
        await message.answer(
            f"Неизвестный режим: <code>{mode_str}</code>\n"
            "Допустимые: <code>auto</code>, <code>semi-auto</code>, <code>manual</code>"
        )
        return

    chat = await conversations.load_chat(settings.state_path, booking_id)
    chat.mode = new_mode
    if new_mode != OperatingMode.SEMI_AUTO:
        chat.draft = None
    await conversations.save_chat(settings.state_path, chat)

    await message.answer(f"Режим для <code>{booking_id}</code>: <b>{new_mode.value}</b>")
    logger.info("Mode changed: booking=%s mode=%s", booking_id, new_mode.value)

    await audit_log(
        "mode", "changed",
        booking_id=booking_id,
        actor="tutor",
        detail={"new_mode": new_mode.value},
    )


# -- Approval inline-button callbacks -----------------------------------------

@router.callback_query(F.data.startswith("appr:"))
async def on_approval_callback(
    callback: CallbackQuery, role: str, settings: Settings,
) -> None:
    if role != "tutor":
        await callback.answer("Нет доступа")
        return

    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Неверный формат")
        return

    _, action, approval_id = parts

    if action == "approve":
        ok = await approval_handler.approve(approval_id, settings)
        if ok:
            await callback.answer("Одобрено и отправлено студенту")
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply("Ответ отправлен студенту.")
        else:
            await callback.answer("Не удалось одобрить (уже обработано?)")

    elif action == "reject":
        ok = await approval_handler.reject(approval_id, settings)
        if ok:
            await callback.answer("Отклонено")
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply("Черновик отклонён.")
        else:
            await callback.answer("Не удалось отклонить (уже обработано?)")

    else:
        await callback.answer("Неизвестное действие")


# -- Tutor reply-to: approval edit or escalation response ----------------------

@router.message(F.text, F.reply_to_message)
async def on_tutor_reply(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    replied_to_id = message.reply_to_message.message_id
    logger.info(
        "Tutor reply received: reply_to_message_id=%s text=%r",
        replied_to_id,
        (message.text or "")[:200],
    )

    # Check if this is a reply to an approval message (edit & approve)
    approval = await approvals.find_pending_by_tutor_message(
        settings.state_path, replied_to_id,
    )
    if approval:
        logger.info(
            "Tutor reply matched approval: approval_id=%s booking=%s",
            approval["approval_id"],
            approval["booking_id"],
        )
        ok = await approval_handler.edit_and_approve(
            approval["approval_id"], message.text, settings,
        )
        if ok:
            await message.answer("Отредактированный ответ отправлен студенту.")
        else:
            await message.answer("Не удалось обработать (уже обработано?).")
        return

    # Otherwise check escalations (existing behaviour)
    escalation = await escalations.find_pending_by_tutor_message(
        settings.state_path, replied_to_id
    )
    if not escalation:
        booking_id = _extract_booking_id_from_message(message.reply_to_message)
        if booking_id:
            logger.info(
                "Tutor reply fallback by booking_id from message text: booking=%s",
                booking_id,
            )
            loaded = await escalations.load(settings.state_path, booking_id)
            if loaded and loaded.get("status") == "pending":
                escalation = loaded
    if not escalation:
        logger.warning(
            "Tutor reply did not match pending escalation: reply_to_message_id=%s text=%r",
            replied_to_id,
            (message.reply_to_message.text or message.reply_to_message.html_text or "")[:200],
        )
        await message.answer(
            "Не удалось сопоставить ответ с активным вопросом. "
            "Ответьте реплаем на последнее сообщение с ID брони."
        )
        return  # not an escalation reply

    logger.info(
        "Tutor reply matched escalation: booking=%s tutor_message_id=%s",
        escalation["booking_id"],
        escalation.get("tutor_message_id"),
    )

    booking_id = escalation["booking_id"]
    booking = await bookings.load(settings.state_path, booking_id)
    if not booking:
        logger.warning("Tutor replied for missing booking: %s", booking_id)
        return

    student_id = booking.get("telegram_user_id")
    if not student_id:
        logger.warning("Tutor reply blocked: booking=%s has no telegram_user_id", booking_id)
        await message.answer("Студент ещё не подключился через deeplink.")
        return

    student_bot = registry.get_student()
    if student_bot is None:
        logger.error("Student bot not available")
        await message.answer("Ошибка: не удалось отправить ответ студенту.")
        return

    sent = await send_student_message(
        bot=student_bot,
        chat_id=student_id,
        text=message.text,
        booking_id=booking_id,
        settings=settings,
        source="tutor_telegram_reply",
        actor="tutor",
    )
    if not sent:
        logger.error("Tutor reply delivery failed: booking=%s student_id=%s", booking_id, student_id)
        await message.answer("Ошибка: не удалось отправить ответ студенту.")
        return

    await escalations.resolve(
        settings.state_path,
        booking_id,
        message.text,
        resolved_by="tutor",
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id,
        escalation_state="resolved",
        escalation_reason=None,
        current_stage="tutor_reply_sent",
        status="active",
        assigned_human="tutor",
        automation_enabled=False,
    )
    await message.answer(templates.tutor_answer_sent())
    logger.info("Tutor reply routed: booking=%s -> student=%s", booking_id, student_id)
    await audit_log(
        "escalation", "resolved",
        booking_id=booking_id,
        actor="tutor",
        detail={"via": "telegram"},
    )


@router.message(F.text)
async def on_tutor_message(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return
    if message.reply_to_message:
        return
    if (message.text or "").startswith("/"):
        return

    knowledge = await knowledge_search(settings.knowledge_path, message.text, limit=5)
    client = OpenclawClient(settings)
    reply = await client.tutor_assistant(message.text, knowledge=knowledge)
    await message.answer(reply, disable_web_page_preview=True)


def _extract_booking_id_from_message(message: Message | None) -> str | None:
    if message is None:
        return None
    candidates = [
        getattr(message, "html_text", None),
        getattr(message, "text", None),
        getattr(message, "caption", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        match = _BOOKING_ID_RE.search(candidate)
        if match:
            return match.group(1)
    return None
