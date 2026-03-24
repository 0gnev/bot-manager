"""
Bot and Dispatcher setup.

Two bots share one Dispatcher:
  - bot_student  (TOKEN_DEFAULT) — student-facing
  - bot_tutor    (TOKEN_MANAGER) — tutor admin

RoleMiddleware injects `role` into every handler based on which bot
received the update. Settings is injected via workflow_data.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bridge.bot import registry
from bridge.bot.middleware import RoleMiddleware
from bridge.bot.handlers import start, messages, tutor
from bridge.config import Settings


def create_bots_and_dispatcher(settings: Settings) -> tuple[Bot, Bot, Dispatcher]:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot_student = Bot(token=settings.telegram_bot_token_default, default=defaults)
    bot_tutor = Bot(token=settings.telegram_bot_token_manager, default=defaults)

    registry.register(bot_student, bot_tutor)

    dp = Dispatcher()
    dp.workflow_data["settings"] = settings
    dp.update.middleware(RoleMiddleware(student_token=settings.telegram_bot_token_default))

    dp.include_router(start.router)
    dp.include_router(messages.router)
    dp.include_router(tutor.router)

    return bot_student, bot_tutor, dp
