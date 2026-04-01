"""
Student message and image handlers.
Routes to openclaw and dispatches the response.
Behavior depends on the chat's operating mode.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message, PhotoSize

from bridge.approvals.handler import submit_for_approval
from bridge.audit import audit_log
from bridge.bot import registry
from bridge.clients.openclaw import OpenclawClient
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.escalation.handler import escalate
from bridge.policies import evaluate_ai_response
from bridge.state import bookings, conversations, load_controls, OperatingMode
from obsidian_adapter.reader import search as knowledge_search
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="messages")


# -- Helpers -------------------------------------------------------------------

async def _notify_tutor(settings: Settings, booking_id: str, text: str) -> None:
    """Send a notification to the tutor via the owner bot."""
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if not tutor_chat_id:
        logger.warning("TUTOR_CHAT_ID not set; cannot notify tutor")
        return
    owner_bot = registry.get_owner()
    if owner_bot is None:
        logger.error("Owner bot not available for tutor notification")
        return
    try:
        await owner_bot.send_message(int(tutor_chat_id), text)
    except Exception as exc:
        logger.error("Failed to notify tutor: %s", exc)


async def _handle_ai_response(
    message: Message,
    booking: dict,
    response: dict,
    settings: Settings,
    booking_id: str,
) -> None:
    """Process an AI response in auto mode."""
    action = response.get("action", "answer")
    content = response.get("content", "")

    if action == "escalate":
        await escalate(message, booking, content, settings)
    elif action == "clarify":
        await send_student_message(
            bot=message.bot,
            chat_id=message.chat.id,
            text=templates.clarify(content),
            booking_id=booking_id,
            settings=settings,
            source="ai_clarify",
            actor="system",
        )
    else:
        await send_student_message(
            bot=message.bot,
            chat_id=message.chat.id,
            text=templates.answer(content),
            booking_id=booking_id,
            settings=settings,
            source="ai_answer",
            actor="system",
        )


async def _handle_semi_auto(
    message: Message,
    booking: dict,
    response: dict,
    settings: Settings,
    booking_id: str,
) -> None:
    """In approval mode: submit draft for tutor review."""
    action = response.get("action", "answer")
    content = response.get("content", "")
    confidence = response.get("confidence", 0.0)

    # Escalations bypass draft approval
    if action == "escalate":
        await escalate(message, booking, content, settings)
        return

    # Submit for approval via the approvals queue
    approval = await submit_for_approval(
        booking_id=booking_id,
        student_chat_id=message.from_user.id,
        draft_content=content,
        action=action,
        confidence=confidence,
        settings=settings,
    )
    if approval is None:
        await escalate(message, booking, content or "Требуется проверка преподавателя.", settings)
        return

    await message.answer(
        "Ваш преподаватель проверит ответ и отправит его вручную."
    )


async def _apply_policy_result(
    message: Message,
    booking: dict,
    response: dict,
    settings: Settings,
    booking_id: str,
    *,
    student_text: str,
) -> None:
    tutor_available = bool(getattr(settings, "tutor_chat_id", None))
    chat = await conversations.load_chat(settings.state_path, booking_id)
    confidence = response.get("confidence")
    decision = evaluate_ai_response(
        response=response,
        booking=booking,
        mode=chat.mode,
        student_text=student_text,
        tutor_available=tutor_available,
    )

    await audit_log(
        "policy",
        "evaluated",
        booking_id=booking_id,
        actor="system",
        detail={
            "route": decision.route,
            "reason": decision.reason,
            "action": response.get("action"),
            "confidence": response.get("confidence"),
        },
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id,
        confidence=confidence,
        current_stage=f"policy_{decision.route}",
    )

    if decision.route == "send":
        await _handle_ai_response(message, booking, response, settings, booking_id)
        return

    if decision.route == "approval":
        await _handle_semi_auto(message, booking, response, settings, booking_id)
        return

    if decision.route == "escalate":
        escalation_text = response.get("content") if response.get("action") == "escalate" else student_text
        await escalate(message, booking, escalation_text or student_text, settings)
        return

    logger.warning("Policy blocked outbound reply: booking=%s reason=%s", booking_id, decision.reason)
    await message.answer("Сейчас я не могу ответить автоматически.")


async def _handle_manual(
    message: Message,
    booking: dict,
    settings: Settings,
    booking_id: str,
    student_text: str,
    *,
    context_label: str = "manual",
) -> None:
    """In manual mode: acknowledge and forward to tutor."""
    await message.answer("Ваш преподаватель ответит в ближайшее время.")

    attendee = booking.get("attendee") or {}
    student_name = attendee.get("name", "Студент")
    notice = (
        f"<b>Сообщение от студента</b> ({context_label})\n"
        f"Студент: {student_name}\n"
        f"ID брони: <code>{booking_id}</code>\n\n"
        f"{student_text}"
    )
    await _notify_tutor(settings, booking_id, notice)


# -- Text messages -------------------------------------------------------------

@router.message(F.text)
async def on_text(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    booking = await bookings.find_by_telegram_user(
        settings.state_path, message.from_user.id
    )
    if not booking:
        await message.answer(templates.booking_not_found())
        return

    booking_id = booking["booking_id"]
    text = message.text
    user_id = str(message.from_user.id)

    await audit_log(
        "message", "student_text_received",
        booking_id=booking_id,
        actor=user_id,
        detail={"length": len(text)},
    )

    await conversations.append(settings.state_path, booking_id, "user", text)

    chat = await conversations.load_chat(settings.state_path, booking_id)
    controls = await load_controls(settings.state_path)
    mode = chat.mode
    await conversations.update_metadata(
        settings.state_path,
        booking_id,
        scenario_type="student_dialogue",
        current_stage="student_message_received",
        status="manual_takeover" if mode == OperatingMode.MANUAL else "active",
    )

    if not controls.get("global_automation_enabled", True):
        await audit_log(
            "automation",
            "blocked_global",
            booking_id=booking_id,
            actor="system",
            detail={"reason": controls.get("reason")},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id,
            current_stage="automation_paused_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            settings,
            booking_id,
            text,
            context_label="global-stop",
        )
        return

    if not chat.automation_enabled:
        await audit_log(
            "automation",
            "blocked_chat",
            booking_id=booking_id,
            actor="system",
            detail={"mode": mode.value},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id,
            current_stage="automation_paused_chat",
            status="manual_takeover",
        )
        await _handle_manual(
            message,
            booking,
            settings,
            booking_id,
            text,
            context_label="chat-stop",
        )
        return

    if mode == OperatingMode.MANUAL:
        await _handle_manual(
            message,
            booking,
            settings,
            booking_id,
            text,
            context_label="manual",
        )
        return

    # AUTO and SEMI_AUTO both call AI
    history = await conversations.load(settings.state_path, booking_id)
    knowledge = await knowledge_search(settings.knowledge_path, text, limit=3)
    client = OpenclawClient(settings)

    response = await client.chat(
        message=text,
        booking_context=booking,
        history=history[:-1],  # exclude the message we just appended
        knowledge=knowledge,
    )

    action = response.get("action", "answer")

    await audit_log(
        "message", "ai_response",
        booking_id=booking_id,
        actor="system",
        detail={"action": action, "confidence": response.get("confidence")},
    )

    await _apply_policy_result(
        message,
        booking,
        response,
        settings,
        booking_id,
        student_text=text,
    )


# -- Photo messages ------------------------------------------------------------

@router.message(F.photo)
async def on_photo(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    booking = await bookings.find_by_telegram_user(
        settings.state_path, message.from_user.id
    )
    if not booking:
        await message.answer(templates.booking_not_found())
        return

    booking_id = booking["booking_id"]

    caption = message.caption or ""
    image_text = f"[image] {caption}" if caption else "[image]"
    await conversations.append(
        settings.state_path, booking_id, "user", image_text
    )

    chat = await conversations.load_chat(settings.state_path, booking_id)
    controls = await load_controls(settings.state_path)
    mode = chat.mode
    await conversations.update_metadata(
        settings.state_path,
        booking_id,
        scenario_type="student_dialogue",
        current_stage="student_image_received",
        status="manual_takeover" if mode == OperatingMode.MANUAL else "active",
    )

    if not controls.get("global_automation_enabled", True):
        await audit_log(
            "automation",
            "blocked_global",
            booking_id=booking_id,
            actor="system",
            detail={"reason": controls.get("reason"), "message_type": "image"},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id,
            current_stage="automation_paused_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            settings,
            booking_id,
            image_text,
            context_label="global-stop",
        )
        return

    if not chat.automation_enabled:
        await audit_log(
            "automation",
            "blocked_chat",
            booking_id=booking_id,
            actor="system",
            detail={"mode": mode.value, "message_type": "image"},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id,
            current_stage="automation_paused_chat",
            status="manual_takeover",
        )
        await _handle_manual(
            message,
            booking,
            settings,
            booking_id,
            image_text,
            context_label="chat-stop",
        )
        return

    if mode == OperatingMode.MANUAL:
        await _handle_manual(
            message,
            booking,
            settings,
            booking_id,
            image_text,
            context_label="manual",
        )
        return

    await message.answer(templates.image_received())

    # Download the largest photo variant
    photo: PhotoSize = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    upload_dir = Path(settings.uploads_path) / booking_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(upload_dir / f"{photo.file_id}.jpg")
    await message.bot.download_file(file.file_path, destination=local_path)

    history = await conversations.load(settings.state_path, booking_id)
    knowledge = await knowledge_search(settings.knowledge_path, caption, limit=3) if caption else []
    client = OpenclawClient(settings)

    response = await client.image(
        image_path=local_path,
        caption=caption,
        booking_context=booking,
        history=history[:-1],  # exclude the image message we just appended
        knowledge=knowledge,
    )

    await _apply_policy_result(
        message,
        booking,
        response,
        settings,
        booking_id,
        student_text=caption or "[image]",
    )
