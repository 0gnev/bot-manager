"""
Bot and Dispatcher setup.

Two bots, one Dispatcher:
  - bot_student  (TOKEN_STUDENT) — open to all users, polled by Bridge
  - bot_owner    (TOKEN_OWNER)   — restricted to tutor whitelist,
                                   polled by OpenClaw; Bridge only sends
                                   escalation notices through it

RoleMiddleware injects `role` into every handler based on which bot
received the update. Settings is injected via workflow_data.

Only student-facing Telegram handlers are mounted here. Tutor/admin control
is handled via the REST API because the owner bot is managed by OpenClaw.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bridge.bot import registry
from bridge.bot.middleware import IdempotencyMiddleware, RoleMiddleware
from bridge.bot.handlers import start, messages
from bridge.config import Settings


def create_bots_and_dispatcher(settings: Settings) -> tuple[Bot, Dispatcher]:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot_student = Bot(token=settings.telegram_bot_token_student, default=defaults)

    # Owner bot is created lazily by registry on first use (to avoid
    # conflicting with OpenClaw's polling of the same token).
    registry.register(bot_student, settings.telegram_bot_token_owner or None)

    dp = Dispatcher()
    dp.workflow_data["settings"] = settings
    dp.update.middleware(IdempotencyMiddleware())
    dp.update.middleware(RoleMiddleware(student_token=settings.telegram_bot_token_student))

    dp.include_router(start.router)
    dp.include_router(messages.router)

    return bot_student, dp
