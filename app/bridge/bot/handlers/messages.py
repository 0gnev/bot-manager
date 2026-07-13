"""
Student message and image handlers.
Routes to the configured LLM provider chain and dispatches the response.
Behavior depends on the chat's operating mode.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message, PhotoSize

from bridge.approvals.handler import submit_for_approval
from bridge.audit import audit_log
from bridge.bot.filters import StudentBotFilter
from bridge.bot import registry
from bridge.llm import LLMService
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.escalation.handler import escalate, prepare_escalation_package
from bridge.policies import evaluate_ai_response
from bridge.state import (
    bookings,
    contacts,
    conversations,
    escalations,
    load_controls,
    normalize_operating_mode,
    OperatingMode,
)
from bridge.timezones import parse_datetime
from obsidian_adapter.reader import search as knowledge_search
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="messages")


# -- Helpers -------------------------------------------------------------------

async def _notify_tutor(settings: Settings, booking_id: str, text: str):
    """Send a notification to the tutor via the owner bot."""
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if not tutor_chat_id:
        logger.warning("TUTOR_CHAT_ID not set; cannot notify tutor")
        return None
    owner_bot = registry.get_owner()
    if owner_bot is None:
        logger.error("Owner bot not available for tutor notification")
        return None
    try:
        return await owner_bot.send_message(int(tutor_chat_id), text)
    except Exception as exc:
        logger.error("Failed to notify tutor: %s", exc)
        return None


async def _handle_ai_response(
    message: Message,
    booking: dict | None,
    contact: dict,
    response: dict,
    settings: Settings,
    booking_id: str | None,
    contact_id: int,
) -> None:
    """Process an AI response in auto mode."""
    action = response.get("action", "answer")
    content = response.get("content", "")

    if action == "escalate":
        await escalate(
            message,
            booking,
            contact,
            content,
            settings,
            reason="model_requested_escalation",
            ai_response=response,
        )
    elif action == "clarify":
        await send_student_message(
            bot=message.bot,
            chat_id=message.chat.id,
            text=templates.clarify(content),
            booking_id=booking_id,
            contact_id=contact_id,
            settings=settings,
            source="ai_clarify",
            model_output=response,
            actor="system",
        )
    else:
        await send_student_message(
            bot=message.bot,
            chat_id=message.chat.id,
            text=templates.answer(content),
            booking_id=booking_id,
            contact_id=contact_id,
            settings=settings,
            source="ai_answer",
            model_output=response,
            actor="system",
        )


async def _handle_semi_auto(
    message: Message,
    booking: dict | None,
    contact: dict,
    response: dict,
    settings: Settings,
    booking_id: str | None,
    contact_id: int,
    student_text: str,
) -> None:
    """In approval mode: submit draft for tutor review."""
    action = response.get("action", "answer")
    content = response.get("content", "")
    confidence = response.get("confidence", 0.0)

    # Escalations bypass draft approval
    if action == "escalate":
        await escalate(
            message,
            booking,
            contact,
            student_text,
            settings,
            reason="model_requested_escalation",
            ai_response=response,
        )
        return

    # Submit for approval via the approvals queue
    approval = await submit_for_approval(
        booking_id=booking_id,
        contact_id=contact_id,
        student_chat_id=message.from_user.id,
        student_question=student_text,
        draft_content=content,
        action=action,
        confidence=confidence,
        settings=settings,
        booking=booking,
        contact=contact,
    )
    if approval is None:
        await escalate(
            message,
            booking,
            contact,
            student_text or content or "Требуется проверка преподавателя.",
            settings,
        )
        return

    await message.answer(
        "Ваш преподаватель проверит ответ и отправит его вручную."
    )


async def _apply_policy_result(
    message: Message,
    booking: dict | None,
    contact: dict,
    response: dict,
    settings: Settings,
    booking_id: str | None,
    contact_id: int,
    *,
    student_text: str,
) -> None:
    tutor_available = bool(getattr(settings, "tutor_chat_id", None))
    chat = await conversations.load_chat_by_contact(
        settings.state_path,
        contact_id,
        booking_id=booking_id,
    )
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
            "contact_id": contact_id,
            "route": decision.route,
            "reason": decision.reason,
            "action": response.get("action"),
            "confidence": response.get("confidence"),
        },
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        confidence=confidence,
        current_stage=f"policy_{decision.route}",
    )

    if decision.route == "send":
        await _handle_ai_response(
            message,
            booking,
            contact,
            response,
            settings,
            booking_id,
            contact_id,
        )
        return

    if decision.route == "approval":
        await _handle_semi_auto(
            message,
            booking,
            contact,
            response,
            settings,
            booking_id,
            contact_id,
            student_text,
        )
        return

    if decision.route == "escalate":
        await escalate(
            message,
            booking,
            contact,
            student_text,
            settings,
            reason=decision.reason,
            ai_response=response,
        )
        return

    logger.warning("Policy blocked outbound reply: booking=%s reason=%s", booking_id, decision.reason)
    if decision.reason == "off_topic_query":
        await message.answer(templates.out_of_scope_question())
        return
    await message.answer("Сейчас я не могу ответить автоматически.")


async def _resume_stale_automation_if_needed(
    settings: Settings,
    *,
    booking_id: str | None,
    contact_id: int,
    chat,
):
    if chat.automation_enabled or chat.mode == OperatingMode.MANUAL:
        return chat

    logger.info(
        "Auto-resuming stale chat automation: booking=%s contact=%s mode=%s",
        booking_id,
        contact_id,
        chat.mode.value,
    )
    return await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        automation_enabled=True,
        status="active",
        current_stage="automation_auto_resumed",
    )


async def _handle_manual(
    message: Message,
    booking: dict | None,
    contact: dict,
    settings: Settings,
    booking_id: str | None,
    contact_id: int,
    student_text: str,
    *,
    context_label: str = "manual",
    notify_student: bool = True,
) -> None:
    """In manual mode: acknowledge and forward to tutor."""
    if notify_student:
        await message.answer("Ваш преподаватель ответит в ближайшее время.")
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    tutor_time_zone = None
    try:
        controls = await load_controls(settings.state_path)
    except Exception:
        logger.warning("Could not load tutor time zone for manual notice", exc_info=True)
    else:
        tutor_time_zone = controls.get("tutor_time_zone")
    reason = {
        "global-stop": "global_automation_disabled",
        "chat-stop": "chat_automation_disabled",
        "manual": "manual_mode",
        "pause-stop": "global_automation_paused",
        "panic-stop": "global_automation_frozen",
    }.get(context_label, "human_review_required")
    notice_context_label = {
        "pause-stop": "pause",
        "panic-stop": "panic",
    }.get(context_label, context_label)
    package = await prepare_escalation_package(
        settings,
        booking=booking,
        contact=contact,
        question=student_text,
        booking_id=booking_id,
        contact_id=contact_id,
        reason=reason,
    )

    attendee = (booking or {}).get("attendee") or {}
    notice = templates.manual_escalation_notice(
        context_label=notice_context_label,
        student_name=attendee.get("name") or contact.get("name") or "Студент",
        booking_id=booking_id,
        question=student_text,
        event_title=(booking or {}).get("title"),
        start_time=_parse_dt((booking or {}).get("start_time")),
        viewer_time_zone=tutor_time_zone,
        student_email=attendee.get("email") or contact.get("email"),
        student_phone=attendee.get("phone") or contact.get("phone"),
        student_telegram=attendee.get("telegram") or contact.get("telegram_username"),
        student_time_zone=attendee.get("timeZone") or contact.get("time_zone"),
        student_telegram_user_id=(booking or {}).get("telegram_user_id") or contact.get("telegram_user_id"),
        summary=package["summary"],
        relevant_history=package["relevant_history"],
        draft_reply=package["draft_reply"],
    )
    sent = await _notify_tutor(settings, booking_id, notice)
    if sent is None:
        return

    await escalations.create(
        settings.state_path,
        booking_id,
        contact_id=contact_id,
        question=student_text,
        tutor_message_id=sent.message_id,
        reason=reason,
        summary=package["summary"],
        relevant_history=package["relevant_history"],
        draft_reply=package["draft_reply"],
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        escalation_state="pending",
        escalation_reason=reason,
        assigned_human=str(tutor_chat_id) if tutor_chat_id else None,
    )


async def _resolve_contact_context(message: Message, settings: Settings) -> tuple[dict, dict | None, list[dict]]:
    contact = await contacts.ensure_telegram_contact(
        settings.state_path,
        message.from_user.id,
        telegram_username=message.from_user.username,
        name=message.from_user.full_name,
    )
    booking = await bookings.resolve_context_for_contact(settings.state_path, contact["id"])
    booking_candidates = await bookings.find_all_by_contact(
        settings.state_path,
        contact["id"],
        active_only=True,
    )
    return contact, booking, booking_candidates


def _build_ai_context(contact: dict, booking: dict | None, booking_candidates: list[dict]) -> dict:
    return {
        "contact": contact,
        "booking": booking,
        "bookings": booking_candidates[:5],
        "context_source": "booking" if booking else "contact_only",
    }


# -- Text messages -------------------------------------------------------------

@router.message(StudentBotFilter(), F.text)
async def on_text(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    contact, booking, booking_candidates = await _resolve_contact_context(message, settings)
    contact_id = contact["id"]
    booking_id = booking["booking_id"] if booking else None
    text = message.text
    user_id = str(message.from_user.id)

    await audit_log(
        "message", "student_text_received",
        booking_id=booking_id,
        actor=user_id,
        detail={"length": len(text), "contact_id": contact_id},
    )

    await conversations.append(
        settings.state_path,
        "user",
        text,
        booking_id=booking_id,
        contact_id=contact_id,
        direction="inbound",
        source="telegram_text",
        delivery_status="received",
        transport_chat_id=message.chat.id,
        transport_message_id=getattr(message, "message_id", None),
    )

    chat = await conversations.load_chat_by_contact(
        settings.state_path,
        contact_id,
        booking_id=booking_id,
    )
    controls = await load_controls(settings.state_path)
    mode = chat.mode
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        scenario_type="student_dialogue",
        current_stage="student_message_received",
        status="manual_takeover" if mode == OperatingMode.MANUAL else "active",
    )
    chat = await _resume_stale_automation_if_needed(
        settings,
        booking_id=booking_id,
        contact_id=contact_id,
        chat=chat,
    )
    operating_mode = normalize_operating_mode(controls.get("operating_mode"))

    if operating_mode == "frozen":
        await audit_log(
            "automation",
            "blocked_emergency",
            booking_id=booking_id,
            actor="system",
            detail={
                "mode": operating_mode,
                "reason": controls.get("incident_reason") or controls.get("reason"),
                "contact_id": contact_id,
            },
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_frozen_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            text,
            context_label="panic-stop",
            notify_student=False,
        )
        return

    if operating_mode == "degraded":
        await audit_log(
            "automation",
            "blocked_emergency",
            booking_id=booking_id,
            actor="system",
            detail={
                "mode": operating_mode,
                "reason": controls.get("incident_reason") or controls.get("reason"),
                "contact_id": contact_id,
            },
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_degraded_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            text,
            context_label="pause-stop",
        )
        return

    if not controls.get("global_automation_enabled", True):
        await audit_log(
            "automation",
            "blocked_global",
            booking_id=booking_id,
            actor="system",
            detail={"reason": controls.get("reason"), "contact_id": contact_id},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_paused_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
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
            detail={"mode": mode.value, "contact_id": contact_id},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_paused_chat",
            status="manual_takeover",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            text,
            context_label="chat-stop",
        )
        return

    if mode == OperatingMode.MANUAL:
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            text,
            context_label="manual",
        )
        return

    # AUTO and SEMI_AUTO both call AI
    history = await conversations.load(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
    )
    knowledge = await knowledge_search(settings.knowledge_path, text, limit=3)
    client = LLMService(settings)

    response = await client.chat(
        message=text,
        booking_context=_build_ai_context(contact, booking, booking_candidates),
        history=history[:-1],  # exclude the message we just appended
        knowledge=knowledge,
    )

    action = response.get("action", "answer")

    await audit_log(
        "message", "ai_response",
        booking_id=booking_id,
        actor="system",
        detail={"action": action, "confidence": response.get("confidence"), "contact_id": contact_id},
    )

    await _apply_policy_result(
        message,
        booking,
        contact,
        response,
        settings,
        booking_id,
        contact_id,
        student_text=text,
    )


def _parse_dt(value: str | None):
    return parse_datetime(value)


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


# -- Photo messages ------------------------------------------------------------

@router.message(StudentBotFilter(), F.photo)
async def on_photo(message: Message, role: str, settings: Settings) -> None:
    if role != "student":
        return

    contact, booking, booking_candidates = await _resolve_contact_context(message, settings)
    contact_id = contact["id"]
    booking_id = booking["booking_id"] if booking else None

    caption = message.caption or ""
    image_text = f"[image] {caption}" if caption else "[image]"
    photo: PhotoSize = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    upload_dir = Path(settings.uploads_path) / (booking_id or f"contact-{contact_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(upload_dir / f"{photo.file_id}.jpg")
    await message.bot.download_file(file.file_path, destination=local_path)

    await conversations.append(
        settings.state_path,
        "user",
        image_text,
        booking_id=booking_id,
        contact_id=contact_id,
        direction="inbound",
        source="telegram_photo",
        delivery_status="received",
        transport_chat_id=message.chat.id,
        transport_message_id=getattr(message, "message_id", None),
        attachments=[
            {
                "file_id": photo.file_id,
                "file_type": "photo",
                "mime_type": "image/jpeg",
                "local_path": local_path,
                "caption": caption or None,
            }
        ],
    )

    chat = await conversations.load_chat_by_contact(
        settings.state_path,
        contact_id,
        booking_id=booking_id,
    )
    controls = await load_controls(settings.state_path)
    mode = chat.mode
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        scenario_type="student_dialogue",
        current_stage="student_image_received",
        status="manual_takeover" if mode == OperatingMode.MANUAL else "active",
    )
    chat = await _resume_stale_automation_if_needed(
        settings,
        booking_id=booking_id,
        contact_id=contact_id,
        chat=chat,
    )
    operating_mode = normalize_operating_mode(controls.get("operating_mode"))

    if operating_mode == "frozen":
        await audit_log(
            "automation",
            "blocked_emergency",
            booking_id=booking_id,
            actor="system",
            detail={
                "mode": operating_mode,
                "reason": controls.get("incident_reason") or controls.get("reason"),
                "message_type": "image",
                "contact_id": contact_id,
            },
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_frozen_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            image_text,
            context_label="panic-stop",
            notify_student=False,
        )
        return

    if operating_mode == "degraded":
        await audit_log(
            "automation",
            "blocked_emergency",
            booking_id=booking_id,
            actor="system",
            detail={
                "mode": operating_mode,
                "reason": controls.get("incident_reason") or controls.get("reason"),
                "message_type": "image",
                "contact_id": contact_id,
            },
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_degraded_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            image_text,
            context_label="pause-stop",
        )
        return

    if not controls.get("global_automation_enabled", True):
        await audit_log(
            "automation",
            "blocked_global",
            booking_id=booking_id,
            actor="system",
            detail={"reason": controls.get("reason"), "message_type": "image", "contact_id": contact_id},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_paused_global",
            status="paused",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
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
            detail={"mode": mode.value, "message_type": "image", "contact_id": contact_id},
        )
        await conversations.update_metadata(
            settings.state_path,
            booking_id=booking_id,
            contact_id=contact_id,
            current_stage="automation_paused_chat",
            status="manual_takeover",
        )
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            image_text,
            context_label="chat-stop",
        )
        return

    if mode == OperatingMode.MANUAL:
        await _handle_manual(
            message,
            booking,
            contact,
            settings,
            booking_id,
            contact_id,
            image_text,
            context_label="manual",
        )
        return

    await message.answer(templates.image_received())

    history = await conversations.load(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
    )
    knowledge = await knowledge_search(settings.knowledge_path, caption, limit=3) if caption else []
    client = LLMService(settings)

    response = await client.image(
        image_path=local_path,
        caption=caption,
        booking_context=_build_ai_context(contact, booking, booking_candidates),
        history=history[:-1],  # exclude the image message we just appended
        knowledge=knowledge,
    )

    await _apply_policy_result(
        message,
        booking,
        contact,
        response,
        settings,
        booking_id,
        contact_id,
        student_text=caption or "[image]",
    )
