"""
Approval lifecycle.

1. AI generates a draft -> submit_for_approval() stores it and notifies tutor.
2. Tutor approves/rejects/edits through the REST API.
3. Bridge sends the final result to the student and updates state.
"""

from __future__ import annotations

import logging

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.config import Settings
from bridge.state import approvals, conversations
from telegram_adapter import templates

logger = logging.getLogger(__name__)


def _format_approval_notice(data: dict) -> str:
    action_label = "Ответ" if data["action"] == "answer" else "Уточнение"
    confidence_pct = int(data["confidence"] * 100)
    return (
        f"<b>Черновик для проверки</b>\n"
        f"Approval ID: <code>{data['approval_id']}</code>\n"
        f"Тип: {action_label} (уверенность: {confidence_pct}%)\n"
        f"Бронь: <code>{data['booking_id']}</code>\n\n"
        f"{data['draft_content']}\n\n"
        "<i>Дальнейшие действия выполняются через REST API "
        "(`/api/approvals` и `/api/tutor`).</i>"
    )


async def submit_for_approval(
    booking_id: str,
    student_chat_id: int,
    draft_content: str,
    action: str,
    confidence: float,
    settings: Settings,
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
        student_chat_id=student_chat_id,
        draft_content=draft_content,
        action=action,
        confidence=confidence,
    )

    notice = _format_approval_notice(data)
    sent = await owner_bot.send_message(tutor_chat_id, notice)

    await approvals.set_tutor_message_id(
        settings.state_path, data["approval_id"], sent.message_id,
    )
    data["tutor_message_id"] = sent.message_id

    await audit_log(
        "approval", "submitted",
        booking_id=booking_id,
        actor="system",
        detail={"approval_id": data["approval_id"], "action": action, "confidence": confidence},
    )

    logger.info(
        "Submitted approval %s for booking %s -> tutor",
        data["approval_id"], booking_id,
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
    await student_bot.send_message(
        data["student_chat_id"], templates.answer(content),
    )
    await conversations.append(
        settings.state_path, data["booking_id"], "assistant", content,
    )
    await approvals.resolve_approval(settings.state_path, approval_id, "approved")

    await audit_log(
        "approval", "approved",
        booking_id=data["booking_id"],
        actor="tutor",
        detail={"approval_id": approval_id},
    )

    logger.info("Approval %s approved -> sent to student", approval_id)
    return True


async def reject(approval_id: str, settings: Settings) -> bool:
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data or data["status"] != "pending":
        return False

    await approvals.resolve_approval(settings.state_path, approval_id, "rejected")

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

    await student_bot.send_message(
        data["student_chat_id"], templates.answer(new_content),
    )
    await conversations.append(
        settings.state_path, data["booking_id"], "assistant", new_content,
    )
    await approvals.resolve_approval(
        settings.state_path, approval_id, "edited", final_content=new_content,
    )

    await audit_log(
        "approval", "edited_and_approved",
        booking_id=data["booking_id"],
        actor="tutor",
        detail={"approval_id": approval_id},
    )

    logger.info("Approval %s edited & approved -> sent to student", approval_id)
    return True
