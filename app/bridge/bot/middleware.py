"""
Middleware that injects the role ("student" | "tutor") into handler data
based on which bot instance received the update.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


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
