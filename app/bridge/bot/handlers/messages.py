"""
Student message and image handlers.
Routes to openclaw and dispatches the response.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message, PhotoSize

from bridge.clients.openclaw import OpenclawClient
from bridge.config import Settings
from bridge.escalation.handler import escalate
from bridge.state import bookings, conversations
from obsidian_adapter.reader import search as knowledge_search
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="messages")


# ── Text messages ─────────────────────────────────────────────────────────────

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

    await conversations.append(settings.state_path, booking_id, "user", text)

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
    content = response.get("content", "")

    if action == "escalate":
        await escalate(message, booking, content, settings)
    elif action == "clarify":
        await message.answer(templates.clarify(content))
        await conversations.append(settings.state_path, booking_id, "assistant", content)
    else:
        await message.answer(templates.answer(content))
        await conversations.append(settings.state_path, booking_id, "assistant", content)


# ── Photo messages ────────────────────────────────────────────────────────────

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
    await message.answer(templates.image_received())

    # Download the largest photo variant
    photo: PhotoSize = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    upload_dir = Path(settings.uploads_path) / booking_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(upload_dir / f"{photo.file_id}.jpg")
    await message.bot.download_file(file.file_path, destination=local_path)

    caption = message.caption or ""
    if caption:
        await conversations.append(
            settings.state_path, booking_id, "user", f"[image] {caption}"
        )

    history = await conversations.load(settings.state_path, booking_id)
    knowledge = await knowledge_search(settings.knowledge_path, caption, limit=3) if caption else []
    client = OpenclawClient(settings)

    response = await client.image(
        image_path=local_path,
        caption=caption,
        booking_context=booking,
        history=history,
        knowledge=knowledge,
    )

    action = response.get("action", "answer")
    content = response.get("content", "")

    if action == "escalate":
        await escalate(message, booking, content or caption, settings, image_path=local_path)
    else:
        await message.answer(templates.answer(content))
        await conversations.append(settings.state_path, booking_id, "assistant", content)
