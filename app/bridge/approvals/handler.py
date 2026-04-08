"""
Approval lifecycle.

1. AI generates a draft -> submit_for_approval() stores it and notifies tutor.
2. Tutor approves/rejects/edits through the REST API.
3. Bridge sends the final result to the student and updates state.
"""

from __future__ import annotations

import logging
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.clients.openclaw import OpenclawClient
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.knowledge_learning import schedule_capture
from bridge.state import approvals, bookings, contacts, conversations
from telegram_adapter import templates

logger = logging.getLogger(__name__)


def _approval_reply_markup(approval_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить студенту",
                    callback_data=f"appr:approve:{approval_id}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"appr:reject:{approval_id}",
                ),
            ]
        ]
    )


def _format_approval_notice(
    data: dict,
    *,
    booking: dict | None = None,
    contact: dict | None = None,
    tutor_instruction: str | None = None,
) -> str:
    action_label = "Ответ" if data["action"] == "answer" else "Уточнение"
    confidence_pct = int(data["confidence"] * 100)
    lines = [
        "<b>Черновик для проверки</b>",
        f"<b>Тип:</b> {action_label} (уверенность: {confidence_pct}%)",
    ]

    student_name = ((booking or {}).get("attendee") or {}).get("name") or (contact or {}).get("name")
    if student_name:
        lines.append(f"<b>Студент:</b> {student_name}")

    if data.get("booking_id"):
        lines.append(f"<b>ID брони:</b> <code>{data['booking_id']}</code>")
    elif data.get("contact_id") is not None:
        lines.append(f"<b>ID контакта:</b> <code>{data['contact_id']}</code>")
        lines.append("<b>Контекст:</b> Общий вопрос без привязки к записи")

    lines.extend(
        [
            "",
            "<b>Текст черновика:</b>",
            escape(data["draft_content"]),
        ]
    )
    if tutor_instruction:
        lines.extend(
            [
                "",
                "<b>Последняя инструкция преподавателя:</b>",
                escape(tutor_instruction),
            ]
        )
    lines.extend(
        [
            "",
            "<i>Ответьте на это сообщение инструкцией, если нужно переработать черновик.</i>",
            "<i>Чтобы сразу отправить свой текст студенту, начните ответ с <code>/send </code>.</i>",
        ]
    )
    return "\n".join(lines)


async def submit_for_approval(
    booking_id: str | None,
    *,
    contact_id: int,
    student_chat_id: int,
    student_question: str | None,
    draft_content: str,
    action: str,
    confidence: float,
    settings: Settings,
    booking: dict | None = None,
    contact: dict | None = None,
) -> dict | None:
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if tutor_chat_id:
        try:
            tutor_chat_id = int(tutor_chat_id)
        except (ValueError, TypeError):
            tutor_chat_id = None

    if not tutor_chat_id:
        logger.warning("TUTOR_CHAT_ID not configured; cannot submit for approval")
        return None

    owner_bot = registry.get_owner()
    if owner_bot is None:
        logger.error("Owner bot not initialised; cannot submit for approval")
        return None

    data = await approvals.create_approval(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        student_chat_id=student_chat_id,
        student_question=student_question,
        draft_content=draft_content,
        action=action,
        confidence=confidence,
    )

    notice = _format_approval_notice(data, booking=booking, contact=contact)
    sent = await owner_bot.send_message(
        tutor_chat_id,
        notice,
        reply_markup=_approval_reply_markup(data["approval_id"]),
    )

    await approvals.set_tutor_message_id(
        settings.state_path, data["approval_id"], sent.message_id,
    )
    data["tutor_message_id"] = sent.message_id
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        current_stage="awaiting_approval",
        status="pending_review",
        confidence=confidence,
    )

    await audit_log(
        "approval", "submitted",
        booking_id=booking_id,
        actor="system",
        detail={
            "approval_id": data["approval_id"],
            "action": action,
            "confidence": confidence,
            "contact_id": contact_id,
        },
    )

    logger.info(
        "Submitted approval %s for booking=%s contact=%s -> tutor",
        data["approval_id"],
        booking_id,
        contact_id,
    )
    return data


async def approve(approval_id: str, settings: Settings) -> bool:
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data or data["status"] != "pending":
        return False

    student_bot = registry.get_student()
    if student_bot is None:
        logger.error("Student bot not available")
        return False

    content = data["draft_content"]
    sent = await send_student_message(
        bot=student_bot,
        chat_id=data["student_chat_id"],
        text=templates.answer(content),
        booking_id=data["booking_id"],
        contact_id=data.get("contact_id"),
        settings=settings,
        source="approval_approved",
        actor="tutor",
    )
    if not sent:
        return False

    await approvals.resolve_approval(
        settings.state_path,
        approval_id,
        "approved",
        reviewer="tutor",
        review_channel="api",
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=data.get("booking_id"),
        contact_id=data.get("contact_id"),
        current_stage="approved_reply_sent",
        status="active",
        confidence=data.get("confidence"),
        escalation_state="none",
        escalation_reason=None,
    )

    await audit_log(
        "approval", "approved",
        booking_id=data["booking_id"],
        actor="tutor",
        detail={"approval_id": approval_id},
    )

    schedule_capture(
        settings=settings,
        source_kind="approval",
        booking_id=data.get("booking_id"),
        contact_id=data.get("contact_id"),
        approval_id=approval_id,
        source_question=data.get("student_question"),
        final_answer=content,
    )

    logger.info("Approval %s approved -> sent to student", approval_id)
    return True


async def reject(approval_id: str, settings: Settings) -> bool:
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data or data["status"] != "pending":
        return False

    await approvals.resolve_approval(
        settings.state_path,
        approval_id,
        "rejected",
        reviewer="tutor",
        review_channel="api",
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=data.get("booking_id"),
        contact_id=data.get("contact_id"),
        current_stage="approval_rejected",
        status="pending_review",
        confidence=data.get("confidence"),
    )

    await audit_log(
        "approval", "rejected",
        booking_id=data["booking_id"],
        actor="tutor",
        detail={"approval_id": approval_id},
    )

    logger.info("Approval %s rejected", approval_id)
    return True


async def edit_and_approve(
    approval_id: str, new_content: str, settings: Settings,
) -> bool:
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data or data["status"] != "pending":
        return False

    student_bot = registry.get_student()
    if student_bot is None:
        logger.error("Student bot not available")
        return False

    sent = await send_student_message(
        bot=student_bot,
        chat_id=data["student_chat_id"],
        text=templates.answer(new_content),
        booking_id=data["booking_id"],
        contact_id=data.get("contact_id"),
        settings=settings,
        source="approval_edited",
        actor="tutor",
    )
    if not sent:
        return False

    await approvals.resolve_approval(
        settings.state_path,
        approval_id,
        "edited",
        final_content=new_content,
        reviewer="tutor",
        review_channel="api",
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=data.get("booking_id"),
        contact_id=data.get("contact_id"),
        current_stage="edited_reply_sent",
        status="active",
        confidence=data.get("confidence"),
        escalation_state="none",
        escalation_reason=None,
    )

    await audit_log(
        "approval", "edited_and_approved",
        booking_id=data["booking_id"],
        actor="tutor",
        detail={"approval_id": approval_id},
    )

    schedule_capture(
        settings=settings,
        source_kind="approval",
        booking_id=data.get("booking_id"),
        contact_id=data.get("contact_id"),
        approval_id=approval_id,
        source_question=data.get("student_question"),
        final_answer=new_content,
    )

    logger.info("Approval %s edited & approved -> sent to student", approval_id)
    return True


def _last_student_message(history: list[dict]) -> str:
    for entry in reversed(history):
        if entry.get("role") == "user" and entry.get("content"):
            return str(entry["content"])
    return ""


def _build_revision_context(
    contact: dict | None,
    booking: dict | None,
    booking_candidates: list[dict],
) -> dict:
    return {
        "contact": contact or {},
        "booking": booking,
        "bookings": booking_candidates[:5],
        "context_source": "booking" if booking else "contact_only",
    }


async def revise_pending_approval(
    approval_id: str,
    instruction: str,
    settings: Settings,
) -> dict | None:
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data or data["status"] != "pending":
        return None

    owner_bot = registry.get_owner()
    if owner_bot is None:
        logger.error("Owner bot not initialised; cannot revise approval")
        return None

    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if tutor_chat_id:
        try:
            tutor_chat_id = int(tutor_chat_id)
        except (ValueError, TypeError):
            tutor_chat_id = None
    if not tutor_chat_id:
        logger.error("TUTOR_CHAT_ID not configured; cannot revise approval")
        return None

    booking = await bookings.load(settings.state_path, data["booking_id"]) if data.get("booking_id") else None
    contact_id = data.get("contact_id") or (booking or {}).get("contact_id")
    contact = await contacts.load(settings.state_path, contact_id) if contact_id is not None else None
    booking_candidates = (
        await bookings.find_all_by_contact(settings.state_path, contact_id, active_only=True)
        if contact_id is not None
        else ([booking] if booking else [])
    )
    history = await conversations.load(
        settings.state_path,
        booking_id=data.get("booking_id"),
        contact_id=contact_id,
    )
    student_message = _last_student_message(history)
    client = OpenclawClient(settings)
    revision = await client.revise_approval_draft(
        student_message=student_message,
        current_draft=data["draft_content"],
        tutor_instruction=instruction,
        booking_context=_build_revision_context(contact, booking, booking_candidates),
    )
    if not revision:
        return None

    previous_tutor_message_id = data.get("tutor_message_id")
    if previous_tutor_message_id:
        try:
            await owner_bot.edit_message_reply_markup(
                chat_id=tutor_chat_id,
                message_id=previous_tutor_message_id,
                reply_markup=None,
            )
        except Exception:
            logger.debug(
                "Failed to clear old approval buttons: approval_id=%s message_id=%s",
                approval_id,
                previous_tutor_message_id,
                exc_info=True,
            )

    notice_data = {
        **data,
        "draft_content": revision["content"],
        "contact_id": contact_id,
    }
    notice = _format_approval_notice(
        notice_data,
        booking=booking,
        contact=contact,
        tutor_instruction=instruction,
    )
    sent_message = await owner_bot.send_message(
        tutor_chat_id,
        notice,
        reply_markup=_approval_reply_markup(approval_id),
    )
    updated = await approvals.update_pending_draft(
        settings.state_path,
        approval_id,
        revision["content"],
        tutor_message_id=sent_message.message_id,
    )
    if updated is None:
        return None

    await conversations.update_metadata(
        settings.state_path,
        booking_id=data.get("booking_id"),
        contact_id=contact_id,
        current_stage="approval_revised",
        status="pending_review",
        confidence=revision.get("confidence", data.get("confidence")),
    )
    await audit_log(
        "approval",
        "revised",
        booking_id=data.get("booking_id"),
        actor="tutor",
        detail={
            "approval_id": approval_id,
            "contact_id": contact_id,
            "instruction": instruction[:500],
        },
    )
    logger.info("Approval %s revised and re-sent to tutor", approval_id)
    return {
        "decision": "revise",
        "content": revision["content"],
        "approval_id": approval_id,
        "tutor_message_id": sent_message.message_id,
    }
