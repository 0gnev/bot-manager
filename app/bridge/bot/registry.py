"""
Simple bot registry — avoids circular imports when other modules
need to send messages via specific bot instances.

Two bots:
  - student bot (TOKEN_STUDENT) — open, polled by Bridge
  - owner bot   (TOKEN_OWNER)   — restricted, polled by Bridge
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

_student_bot: Bot | None = None
_owner_bot: Bot | None = None
_owner_token: str | None = None


def register(
    student_bot: Bot,
    owner_token: str | None = None,
    owner_bot: Bot | None = None,
) -> None:
    global _student_bot, _owner_token, _owner_bot
    _student_bot = student_bot
    _owner_token = owner_token
    _owner_bot = owner_bot


def get_student() -> Bot | None:
    return _student_bot


def get_owner() -> Bot | None:
    """Return owner bot, creating it lazily only if needed."""
    global _owner_bot
    if _owner_bot is None and _owner_token:
        _owner_bot = Bot(
            token=_owner_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _owner_bot
