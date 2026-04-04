"""
Bot and Dispatcher setup.

Two bots, one Dispatcher:
  - bot_student  (TOKEN_STUDENT) — open to all users, polled by Bridge
  - bot_owner    (TOKEN_OWNER)   — restricted to tutor whitelist,
                                   polled by Bridge for tutor/operator actions

RoleMiddleware injects `role` into every handler based on which bot
received the update. Settings is injected via workflow_data.

Both student and tutor-facing Telegram handlers are mounted here.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bridge.bot import registry
from bridge.bot.middleware import IdempotencyMiddleware, RoleMiddleware
from bridge.bot.handlers import start, messages, tutor
from bridge.config import Settings


def create_bots_and_dispatcher(settings: Settings) -> tuple[Bot, Bot | None, Dispatcher]:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot_student = Bot(token=settings.telegram_bot_token_student, default=defaults)
    bot_owner = None
    if settings.telegram_bot_token_owner:
        bot_owner = Bot(token=settings.telegram_bot_token_owner, default=defaults)

    registry.register(
        bot_student,
        settings.telegram_bot_token_owner or None,
        owner_bot=bot_owner,
    )

    dp = Dispatcher()
    dp.workflow_data["settings"] = settings
    dp.update.middleware(IdempotencyMiddleware())
    dp.update.middleware(RoleMiddleware(student_token=settings.telegram_bot_token_student))

    dp.include_router(start.router)
    dp.include_router(messages.router)
    dp.include_router(tutor.router)

    return bot_student, bot_owner, dp
