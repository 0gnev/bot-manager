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
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

_student_bot: Bot | None = None
_owner_bot: Bot | None = None
_owner_token: str | None = None
_telegram_api_base_url: str | None = None


def build_bot(token: str, telegram_api_base_url: str | None = None) -> Bot:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    if telegram_api_base_url:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(telegram_api_base_url.rstrip("/"))
        )
        return Bot(token=token, default=defaults, session=session)
    return Bot(token=token, default=defaults)


def register(
    student_bot: Bot,
    owner_token: str | None = None,
    owner_bot: Bot | None = None,
    telegram_api_base_url: str | None = None,
) -> None:
    global _student_bot, _owner_token, _owner_bot, _telegram_api_base_url
    _student_bot = student_bot
    _owner_token = owner_token
    _owner_bot = owner_bot
    _telegram_api_base_url = telegram_api_base_url


def get_student() -> Bot | None:
    return _student_bot


def get_owner() -> Bot | None:
    """Return owner bot, creating it lazily only if needed."""
    global _owner_bot
    if _owner_bot is None and _owner_token:
        _owner_bot = build_bot(_owner_token, _telegram_api_base_url)
    return _owner_bot
