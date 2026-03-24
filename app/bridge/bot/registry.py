"""
Simple bot registry — avoids circular imports when other modules
need to send messages via specific bot instances.
"""

from __future__ import annotations

from aiogram import Bot

_student_bot: Bot | None = None
_tutor_bot: Bot | None = None


def register(student_bot: Bot, tutor_bot: Bot) -> None:
    global _student_bot, _tutor_bot
    _student_bot = student_bot
    _tutor_bot = tutor_bot


def get_student() -> Bot | None:
    return _student_bot


def get_tutor() -> Bot | None:
    return _tutor_bot
