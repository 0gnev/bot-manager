"""
Middleware that injects the role ("student" | "tutor") into handler data
based on which bot instance received the update.

Also provides IdempotencyMiddleware to skip duplicate Telegram updates.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from bridge.idempotency import abandon_processing, begin_processing, finish_processing

logger = logging.getLogger(__name__)


class RoleMiddleware(BaseMiddleware):
    def __init__(self, student_token: str) -> None:
        self._student_token = student_token

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data.get("bot")
        data["role"] = "student" if (bot and bot.token == self._student_token) else "tutor"
        return await handler(event, data)


class IdempotencyMiddleware(BaseMiddleware):
    """Skip duplicate Telegram updates using update_id as key."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update: Update | None = data.get("event_update") or (
            event if isinstance(event, Update) else None
        )
        if update is None or not hasattr(update, "update_id"):
            return await handler(event, data)

        key = f"tg:{update.update_id}"
        if await begin_processing(key):
            logger.debug("Duplicate Telegram update ignored: %s", update.update_id)
            return None

        try:
            result = await handler(event, data)
        except Exception:
            await abandon_processing(key)
            raise
        await finish_processing(key)
        return result
