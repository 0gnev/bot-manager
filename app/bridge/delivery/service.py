"""
Unified outbound delivery service for student-facing messages.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import FSInputFile

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
    model_output: dict | None = None,
    photo_path: str | None = None,
    caption: str | None = None,
    attachments: list[dict] | None = None,
) -> bool:
    """Send one student-facing message and record audit/history consistently."""
    attempts = max(int(getattr(settings, "telegram_delivery_attempts", 1) or 1), 1)
    backoff = max(float(getattr(settings, "telegram_delivery_backoff_seconds", 0.0) or 0.0), 0.0)
    message_type = "photo" if photo_path else "text"

    try:
        sent_message = None
        for attempt in range(1, attempts + 1):
            try:
                if photo_path:
                    sent_message = await bot.send_photo(
                        chat_id,
                        FSInputFile(photo_path),
                        caption=caption or None,
                    )
                else:
                    kwargs: dict = {}
                    if disable_web_page_preview is not None:
                        kwargs["disable_web_page_preview"] = disable_web_page_preview
                    sent_message = await bot.send_message(chat_id, text, **kwargs)
                break
            except Exception as exc:
                if attempt >= attempts:
                    raise
                logger.warning(
                    "Retrying student delivery: booking=%s chat_id=%s source=%s type=%s attempt=%s/%s error=%s",
                    booking_id,
                    chat_id,
                    source,
                    message_type,
                    attempt,
                    attempts,
                    exc,
                )
                if backoff > 0:
                    await asyncio.sleep(backoff * (2 ** (attempt - 1)))

        if record_history:
            await conversations.append(
                settings.state_path,
                history_role,
                text,
                booking_id=booking_id,
                contact_id=contact_id,
                direction="outbound",
                source=source,
                delivery_status="sent",
                transport="telegram",
                transport_chat_id=chat_id,
                transport_message_id=getattr(sent_message, "message_id", None),
                delivery_attempts=attempt,
                delivery_recipient=str(chat_id),
                delivery_payload={"message_type": message_type},
                model_output=model_output,
                attachments=attachments,
            )

        await audit_log(
            "delivery",
            "student_message_sent",
            booking_id=booking_id,
            actor=actor,
            detail={
                "source": source,
                "chat_id": chat_id,
                "attempts": attempt,
                "message_type": message_type,
            },
        )
        return True
    except Exception as exc:
        if record_history:
            await conversations.append(
                settings.state_path,
                history_role,
                text,
                booking_id=booking_id,
                contact_id=contact_id,
                direction="outbound",
                source=source,
                delivery_status="failed",
                transport="telegram",
                transport_chat_id=chat_id,
                delivery_attempts=attempts,
                delivery_recipient=str(chat_id),
                delivery_error_text=str(exc)[:500],
                delivery_payload={"message_type": message_type},
                model_output=model_output,
                attachments=attachments,
            )
        logger.error(
            "Failed to send student-facing message: booking=%s chat_id=%s source=%s type=%s error=%s",
            booking_id,
            chat_id,
            source,
            message_type,
            exc,
        )
        await audit_log(
            "delivery",
            "student_message_sent",
            booking_id=booking_id,
            actor=actor,
            outcome="failure",
            detail={
                "source": source,
                "chat_id": chat_id,
                "message_type": message_type,
                "error": str(exc)[:200],
            },
        )
        return False
