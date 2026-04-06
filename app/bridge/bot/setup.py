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

from bridge.bot import registry
from bridge.bot.handlers import start, messages, tutor
from bridge.bot.middleware import IdempotencyMiddleware, RoleMiddleware
from bridge.config import Settings


def create_bots_and_dispatcher(settings: Settings) -> tuple[Bot, Bot | None, Dispatcher]:
    telegram_api_base_url = getattr(settings, "telegram_api_base_url", "") or None
    bot_student = registry.build_bot(
        settings.telegram_bot_token_student,
        telegram_api_base_url,
    )
    bot_owner = None
    if settings.telegram_bot_token_owner:
        bot_owner = registry.build_bot(
            settings.telegram_bot_token_owner,
            telegram_api_base_url,
        )

    registry.register(
        bot_student,
        settings.telegram_bot_token_owner or None,
        owner_bot=bot_owner,
        telegram_api_base_url=telegram_api_base_url,
    )

    dp = Dispatcher()
    dp.workflow_data["settings"] = settings
    dp.update.middleware(IdempotencyMiddleware())
    dp.update.middleware(RoleMiddleware(student_token=settings.telegram_bot_token_student))

    dp.include_router(start.router)
    dp.include_router(messages.router)
    dp.include_router(tutor.router)

    return bot_student, bot_owner, dp
