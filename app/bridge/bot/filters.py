"""
Bot-scoped aiogram filters.

These prevent student-facing and tutor-facing handlers from competing for the
same generic Telegram updates when both bots share one Dispatcher.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.filters import BaseFilter

from bridge.bot import registry


class StudentBotFilter(BaseFilter):
    async def __call__(self, bot: Bot) -> bool:
        student_bot = registry.get_student()
        return bool(student_bot and bot.token == student_bot.token)


class TutorBotFilter(BaseFilter):
    async def __call__(self, bot: Bot) -> bool:
        student_bot = registry.get_student()
        return bool(student_bot and bot.token != student_bot.token)
