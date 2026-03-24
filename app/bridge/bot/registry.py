"""
Simple bot registry — avoids circular imports when other modules
need to send messages via specific bot instances.

The tutor bot is created lazily (on first use) to avoid interfering
with OpenClaw's polling of the same token.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

_student_bot: Bot | None = None
_tutor_bot: Bot | None = None
_tutor_token: str | None = None


def register(student_bot: Bot, tutor_token: str | None = None) -> None:
    global _student_bot, _tutor_token
    _student_bot = student_bot
    _tutor_token = tutor_token


def get_student() -> Bot | None:
    return _student_bot


def get_tutor() -> Bot | None:
    """Lazily create tutor bot instance only when needed for sending."""
    global _tutor_bot
    if _tutor_bot is None and _tutor_token:
        _tutor_bot = Bot(
            token=_tutor_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _tutor_bot
