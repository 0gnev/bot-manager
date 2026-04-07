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
from typing import Any

from aiogram.types import Message

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.config import Settings
from bridge.state import conversations, escalations
from telegram_adapter import templates

logger = logging.getLogger(__name__)


async def escalate(
    message: Message,
    booking: dict | None,
    contact: dict,
    question: str,
    settings: Settings,
    image_path: str | None = None,
    *,
    reason: str | None = "human_review_required",
    ai_response: dict[str, Any] | None = None,
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

    booking_id = booking["booking_id"] if booking else None
    contact_id = contact["id"]
    attendee = (booking or {}).get("attendee") or {}
    package = await prepare_escalation_package(
        settings,
        booking=booking,
        contact=contact,
        question=question,
        booking_id=booking_id,
        contact_id=contact_id,
        reason=reason,
        ai_response=ai_response,
    )

    notice = templates.escalation_notice(
        student_name=attendee.get("name") or contact.get("name") or "Студент",
        booking_id=booking_id,
        question=question,
        event_title=(booking or {}).get("title"),
        start_time=_parse_dt((booking or {}).get("start_time")),
        student_email=attendee.get("email") or contact.get("email"),
        student_phone=attendee.get("phone") or contact.get("phone"),
        student_telegram=attendee.get("telegram") or contact.get("telegram_username"),
        student_time_zone=attendee.get("timeZone") or contact.get("time_zone"),
        student_telegram_user_id=(booking or {}).get("telegram_user_id") or contact.get("telegram_user_id"),
        summary=package["summary"],
        relevant_history=package["relevant_history"],
        draft_reply=package["draft_reply"],
    )

    sent = await owner_bot.send_message(tutor_chat_id, notice)

    if image_path:
        try:
            with open(image_path, "rb") as f:
                await owner_bot.send_photo(
                    tutor_chat_id,
                    f,
                    caption=_tutor_image_caption(booking_id=booking_id, contact_id=contact_id),
                    reply_to_message_id=sent.message_id,
                )
        except Exception as exc:
            logger.warning("Could not forward image to tutor: %s", exc)

    await escalations.create(
        settings.state_path,
        booking_id,
        contact_id=contact_id,
        question=question,
        tutor_message_id=sent.message_id,
        reason=reason or "human_review_required",
        summary=package["summary"],
        relevant_history=package["relevant_history"],
        draft_reply=package["draft_reply"],
    )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        escalation_state="pending",
        escalation_reason=reason or "human_review_required",
        current_stage="awaiting_tutor_reply",
        status="escalated",
        assigned_human=str(tutor_chat_id),
    )

    await message.answer(templates.escalated_to_tutor())
    logger.info("Escalated booking=%s contact=%s → tutor chat=%s", booking_id, contact_id, tutor_chat_id)
    await audit_log(
        "escalation", "created",
        booking_id=booking_id,
        actor=str(message.from_user.id),
        detail={
            "has_image": image_path is not None,
            "contact_id": contact_id,
            "has_draft_reply": bool(package["draft_reply"]),
        },
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _tutor_image_caption(*, booking_id: str | None, contact_id: int) -> str:
    lines = ["Изображение от студента"]
    if booking_id:
        lines.append(f"ID брони: {booking_id}")
    lines.append(f"ID контакта: {contact_id}")
    return "\n".join(lines)


async def prepare_escalation_package(
    settings: Settings,
    *,
    booking: dict | None,
    contact: dict,
    question: str,
    booking_id: str | None,
    contact_id: int,
    reason: str | None,
    ai_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chat = await conversations.load_chat_by_contact(
        settings.state_path,
        contact_id,
        booking_id=booking_id,
    )
    history = await conversations.load(
        settings.state_path,
        booking_id,
        contact_id=contact_id,
    )
    return {
        "summary": _build_summary(
            booking=booking,
            contact=contact,
            reason=reason,
            chat=chat,
        ),
        "relevant_history": _select_relevant_history(history, question),
        "draft_reply": _extract_draft_reply(ai_response),
    }


def _build_summary(
    *,
    booking: dict | None,
    contact: dict,
    reason: str | None,
    chat,
) -> str:
    parts: list[str] = []
    if booking:
        parts.append(
            "Контекст: "
            f"{booking.get('title') or 'Занятие'} "
            f"({templates.fmt_dt(_parse_dt(booking.get('start_time')))})"
        )
    else:
        parts.append(
            "Контекст: общий вопрос"
            f" от {contact.get('name') or contact.get('telegram_username') or 'студента'}"
        )

    parts.append(f"Причина: {_reason_label(reason)}")

    mode = getattr(getattr(chat, "mode", None), "value", getattr(chat, "mode", None))
    if mode:
        parts.append(f"Режим чата: {mode}")

    confidence = getattr(chat, "confidence", None)
    if isinstance(confidence, (float, int)):
        parts.append(f"Последняя уверенность AI: {int(confidence * 100)}%")

    return ". ".join(part.rstrip(".") for part in parts if part).strip()


def _reason_label(reason: str | None) -> str:
    mapping = {
        "human_review_required": "нужна проверка преподавателя",
        "model_requested_escalation": "модель запросила преподавателя",
        "global_automation_disabled": "глобальная автоматизация отключена",
        "chat_automation_disabled": "автоматизация чата отключена",
        "manual_mode": "чат переведён в ручной режим",
        "policy_low_confidence": "низкая уверенность ответа",
        "policy_stop_trigger": "обнаружен стоп-триггер",
        "policy_risky_topic": "требуется ручная проверка",
    }
    if not reason:
        return mapping["human_review_required"]
    return mapping.get(reason, reason.replace("_", " "))


def _select_relevant_history(history: list[dict], question: str, *, limit: int = 4) -> list[dict]:
    items: list[dict] = []
    normalized_question = (question or "").strip()

    for msg in history:
        content = (msg.get("content") or "").strip()
        if not content:
            continue

        if msg.get("direction") == "outbound" and msg.get("delivery_status") not in {
            None,
            "sent",
            "delivered",
        }:
            continue

        items.append(
            {
                "role": msg.get("role"),
                "content": _truncate(content, 280),
                "ts": msg.get("ts"),
            }
        )

    if items and items[-1].get("role") == "user" and items[-1].get("content") == normalized_question:
        items = items[:-1]

    return items[-limit:]


def _extract_draft_reply(ai_response: dict[str, Any] | None) -> str | None:
    if not isinstance(ai_response, dict):
        return None
    if ai_response.get("action") not in {"answer", "clarify"}:
        return None
    content = (ai_response.get("content") or "").strip()
    return content or None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
