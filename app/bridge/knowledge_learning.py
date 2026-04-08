"""
Tutor-approved self-learning flow.

The bot may propose a reusable knowledge note from a tutor-approved answer, but
static knowledge is updated only after explicit tutor approval.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.clients.openclaw import OpenclawClient
from bridge.config import Settings
from bridge.db import get_pool
from bridge.state import bookings, contacts
from bridge.state import knowledge_suggestions as suggestions_state
from telegram_adapter import templates

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _reply_markup(suggestion_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сохранить в базу знаний",
                    callback_data=f"know:approve:{suggestion_id}",
                ),
                InlineKeyboardButton(
                    text="Не сохранять",
                    callback_data=f"know:reject:{suggestion_id}",
                ),
            ]
        ]
    )


def _slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = (
        slug.replace("ё", "e")
        .replace("й", "i")
        .replace("ц", "ts")
        .replace("у", "u")
        .replace("к", "k")
        .replace("е", "e")
        .replace("н", "n")
        .replace("г", "g")
        .replace("ш", "sh")
        .replace("щ", "sch")
        .replace("з", "z")
        .replace("х", "h")
        .replace("ъ", "")
        .replace("ф", "f")
        .replace("ы", "y")
        .replace("в", "v")
        .replace("а", "a")
        .replace("п", "p")
        .replace("р", "r")
        .replace("о", "o")
        .replace("л", "l")
        .replace("д", "d")
        .replace("ж", "zh")
        .replace("э", "e")
        .replace("я", "ya")
        .replace("ч", "ch")
        .replace("с", "s")
        .replace("м", "m")
        .replace("и", "i")
        .replace("т", "t")
        .replace("ь", "")
        .replace("б", "b")
        .replace("ю", "yu")
    )
    slug = _SLUG_RE.sub("-", slug).strip("-")
    return slug or "knowledge-note"


def _default_relative_path(title: str) -> str:
    return f"approved/{_slugify(title)}.md"


def _allocate_relative_path(knowledge_path: str, suggested_file_path: str | None, title: str, suggestion_id: int) -> str:
    base = Path(knowledge_path)
    preferred = suggested_file_path or _default_relative_path(title)
    target = (base / preferred).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        preferred = _default_relative_path(title)
        target = (base / preferred).resolve()

    if not target.exists():
        return str(target.relative_to(base))

    stem = target.stem
    suffix = target.suffix or ".md"
    candidate = target.with_name(f"{stem}-{suggestion_id}{suffix}")
    return str(candidate.relative_to(base))


def schedule_capture(
    *,
    settings: Settings,
    source_kind: str,
    source_question: str | None,
    final_answer: str,
    booking_id: str | None = None,
    contact_id: int | None = None,
    approval_id: str | None = None,
    escalation_id: int | None = None,
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Cannot schedule knowledge suggestion capture outside running loop")
        return

    task = loop.create_task(
        capture(
            settings=settings,
            source_kind=source_kind,
            source_question=source_question,
            final_answer=final_answer,
            booking_id=booking_id,
            contact_id=contact_id,
            approval_id=approval_id,
            escalation_id=escalation_id,
        )
    )
    task.add_done_callback(_log_task_failure)


def _log_task_failure(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("Background knowledge suggestion capture failed")


async def capture(
    *,
    settings: Settings,
    source_kind: str,
    source_question: str | None,
    final_answer: str,
    booking_id: str | None = None,
    contact_id: int | None = None,
    approval_id: str | None = None,
    escalation_id: int | None = None,
) -> dict | None:
    if not final_answer or not final_answer.strip():
        return None

    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if tutor_chat_id:
        try:
            tutor_chat_id = int(tutor_chat_id)
        except (ValueError, TypeError):
            tutor_chat_id = None
    if not tutor_chat_id:
        return None

    owner_bot = registry.get_owner()
    if owner_bot is None:
        return None

    booking = await bookings.load(settings.state_path, booking_id) if booking_id else None
    contact = await contacts.load(settings.state_path, contact_id) if contact_id is not None else None

    candidate = await OpenclawClient(settings).propose_knowledge_candidate(
        source_question=source_question,
        final_answer=final_answer,
        booking_context={
            "booking": booking,
            "contact": contact,
            "context_source": "booking" if booking else "contact_only",
        },
    )
    if not candidate:
        return None

    if not candidate.get("should_save"):
        await audit_log(
            "knowledge",
            "suggestion_skipped",
            booking_id=booking_id,
            actor="system",
            detail={
                "source_kind": source_kind,
                "contact_id": contact_id,
                "approval_id": approval_id,
                "escalation_id": escalation_id,
                "reason": candidate.get("reason"),
            },
        )
        return None

    suggested_path = _default_relative_path(candidate["title"])
    suggestion = await suggestions_state.create(
        settings.state_path,
        source_kind=source_kind,
        booking_id=booking_id,
        contact_id=contact_id,
        approval_id=approval_id,
        escalation_id=escalation_id,
        source_question=source_question,
        answer_text=final_answer,
        title=candidate["title"],
        rationale=candidate.get("reason"),
        content_markdown=candidate["content_markdown"],
        suggested_file_path=suggested_path,
    )

    notice = templates.knowledge_suggestion_notice(
        suggestion,
        booking=booking,
        contact=contact,
    )
    sent = await owner_bot.send_message(
        tutor_chat_id,
        notice,
        reply_markup=_reply_markup(int(suggestion["id"])),
    )
    await suggestions_state.set_tutor_message_id(
        settings.state_path,
        int(suggestion["id"]),
        sent.message_id,
    )

    await audit_log(
        "knowledge",
        "suggested",
        booking_id=booking_id,
        actor="system",
        detail={
            "suggestion_id": suggestion["id"],
            "source_kind": source_kind,
            "contact_id": contact_id,
            "approval_id": approval_id,
            "escalation_id": escalation_id,
            "suggested_file_path": suggested_path,
        },
    )
    return suggestion


async def approve_suggestion(suggestion_id: int, settings: Settings) -> str | None:
    suggestion = await suggestions_state.load(settings.state_path, suggestion_id)
    if not suggestion or suggestion["status"] != "pending":
        return None

    knowledge_dir = Path(settings.knowledge_path)
    relative_path = _allocate_relative_path(
        settings.knowledge_path,
        suggestion.get("suggested_file_path"),
        suggestion["title"],
        suggestion_id,
    )
    target = (knowledge_dir / relative_path).resolve()
    try:
        target.relative_to(knowledge_dir.resolve())
    except ValueError:
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(suggestion["content_markdown"], encoding="utf-8")

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge_updates (file_path, action, content_before, content_after, requested_by, approved)
        VALUES ($1, 'create', NULL, $2, 'tutor', TRUE)
        """,
        relative_path,
        suggestion["content_markdown"],
    )

    await suggestions_state.resolve(
        settings.state_path,
        suggestion_id,
        status="saved",
        resolved_by="tutor",
        knowledge_file_path=relative_path,
    )
    await audit_log(
        "knowledge",
        "suggestion_approved",
        booking_id=suggestion.get("booking_id"),
        actor="tutor",
        detail={
            "suggestion_id": suggestion_id,
            "contact_id": suggestion.get("contact_id"),
            "file_path": relative_path,
        },
    )
    return relative_path


async def reject_suggestion(suggestion_id: int, settings: Settings) -> bool:
    suggestion = await suggestions_state.load(settings.state_path, suggestion_id)
    if not suggestion or suggestion["status"] != "pending":
        return False

    await suggestions_state.resolve(
        settings.state_path,
        suggestion_id,
        status="rejected",
        resolved_by="tutor",
    )
    await audit_log(
        "knowledge",
        "suggestion_rejected",
        booking_id=suggestion.get("booking_id"),
        actor="tutor",
        detail={
            "suggestion_id": suggestion_id,
            "contact_id": suggestion.get("contact_id"),
        },
    )
    return True
