"""
Unified outbound delivery service for student-facing messages.
"""

from __future__ import annotations

import logging

from aiogram import Bot

from bridge.audit import audit_log
from bridge.config import Settings
from bridge.state import conversations

logger = logging.getLogger(__name__)


async def send_student_message(
    *,
    bot: Bot,
    chat_id: int,
    text: str,
    booking_id: str | None,
    settings: Settings,
    source: str,
    contact_id: int | None = None,
    actor: str = "system",
    history_role: str = "assistant",
    record_history: bool = True,
    disable_web_page_preview: bool | None = None,
) -> bool:
    """Send one student-facing message and record audit/history consistently."""
    try:
        kwargs: dict = {}
        if disable_web_page_preview is not None:
            kwargs["disable_web_page_preview"] = disable_web_page_preview
        await bot.send_message(chat_id, text, **kwargs)

        if record_history:
            await conversations.append(
                settings.state_path,
                history_role,
                text,
                booking_id=booking_id,
                contact_id=contact_id,
            )

        await audit_log(
            "delivery",
            "student_message_sent",
            booking_id=booking_id,
            actor=actor,
            detail={"source": source, "chat_id": chat_id},
        )
        return True
    except Exception as exc:
        logger.error(
            "Failed to send student-facing message: booking=%s chat_id=%s source=%s error=%s",
            booking_id,
            chat_id,
            source,
            exc,
        )
        await audit_log(
            "delivery",
            "student_message_sent",
            booking_id=booking_id,
            actor=actor,
            outcome="failure",
            detail={"source": source, "chat_id": chat_id, "error": str(exc)[:200]},
        )
        return False
