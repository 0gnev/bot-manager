"""
Tutor-side handlers.

The tutor replies to an escalation by replying to the forwarded message.
Bridge detects the reply-to message ID, looks up the escalation, and routes
the answer back to the student via the student bot.

Additional features:
  - /mode {booking_id} auto|semi-auto|manual — switch operating mode
  - Approval inline-button callbacks (approve/reject)
  - Reply-to approval message = revise draft, unless prefixed with /send
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, PhotoSize

from bridge.approvals import handler as approval_handler
from bridge.audit import audit_log
from bridge.bot import registry
from bridge.bot.filters import TutorBotFilter
from bridge.clients.openclaw import OpenclawClient
from bridge.config import Settings
from bridge.delivery import send_student_message
from bridge.state import (
    approvals,
    bookings,
    contacts,
    conversations,
    escalations,
    load_controls,
    OperatingMode,
    save_tutor_time_zone,
)
from bridge.timezones import format_datetime, parse_datetime, validate_time_zone_name
from obsidian_adapter.reader import search as knowledge_search
from telegram_adapter import templates

logger = logging.getLogger(__name__)
router = Router(name="tutor")

_BOOKING_ID_RE = re.compile(r"ID брони:\s*(?:<code>)?([A-Za-z0-9_-]+)")
_CONTACT_ID_RE = re.compile(r"ID контакта:\s*(?:<code>)?(\d+)")
_APPROVAL_SEND_RE = re.compile(r"^/send(?:\s+|\n+)(.+)$", re.DOTALL)
_DIALOG_EXPORT_INTENT_RE = re.compile(
    r"(?:\b(?:весь|полный)\s+(?:диалог|чат|лог|текст\s+диалога)\b"
    r"|\bвсю\s+переписк[ау]\b"
    r"|\bистори(?:я|ю)\s+(?:диалога|переписки)\b)",
    re.IGNORECASE,
)
_DIALOG_EXPORT_VERB_RE = re.compile(
    r"\b(?:отправ(?:ь|ить)?|пришл(?:и|ать)?|выгруз(?:и|ить)?|"
    r"покаж(?:и|и-ка|ите)?|дай|скинь)\b",
    re.IGNORECASE,
)
_DIALOG_EXPORT_NOUN_RE = re.compile(
    r"\b(?:диалог|чат|переписк(?:а|у|и)?|истори(?:я|ю)|лог)\b",
    re.IGNORECASE,
)
_DIALOG_EXPORT_TARGET_RE = re.compile(
    r"(?:\b(?:весь|полный)\s+(?:диалог|чат|лог|текст\s+диалога)\b"
    r"|\bвсю\s+переписк[ау]\b"
    r"|\bистори(?:я|ю)\s+(?:диалога|переписки)\b)"
    r"(?:\s+с)?\s+(?P<target>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_EXPORT_TRAILING_RE = re.compile(
    r"(?:\s+(?:сюда|в\s+чат|в\s+телеграм|телеграмом|текстом|в\s+виде\s+текста|"
    r"в\s+тексте|файлом|пожалуйста|плиз))+$",
    re.IGNORECASE,
)
_DIRECT_TUTOR_SOURCES = {
    "tutor_telegram_reply",
    "tutor_api_reply",
    "approval_edit_approve",
}
_REVIEWED_TUTOR_SOURCES = {
    "approval_approved",
}


# -- /mode command -------------------------------------------------------------

@router.message(TutorBotFilter(), Command("mode"))
async def on_mode(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/mode {booking_id} auto|semi-auto|manual</code>"
        )
        return

    booking_id = parts[1]
    mode_str = parts[2].strip().lower()

    try:
        new_mode = OperatingMode(mode_str)
    except ValueError:
        await message.answer(
            f"Неизвестный режим: <code>{mode_str}</code>\n"
            "Допустимые: <code>auto</code>, <code>semi-auto</code>, <code>manual</code>"
        )
        return

    chat = await conversations.load_chat(settings.state_path, booking_id)
    chat.mode = new_mode
    if new_mode != OperatingMode.SEMI_AUTO:
        chat.draft = None
    await conversations.save_chat(settings.state_path, chat)

    await message.answer(f"Режим для <code>{booking_id}</code>: <b>{new_mode.value}</b>")
    logger.info("Mode changed: booking=%s mode=%s", booking_id, new_mode.value)

    await audit_log(
        "mode", "changed",
        booking_id=booking_id,
        actor="tutor",
        detail={"new_mode": new_mode.value},
    )


@router.message(TutorBotFilter(), Command("dialog"))
async def on_dialog_export(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Использование: <code>/dialog username|email|имя|booking_id</code>"
        )
        return

    await _maybe_export_dialogue(
        message,
        settings,
        request_text=parts[1].strip(),
        force=True,
    )


@router.message(TutorBotFilter(), Command("timezone"))
async def on_timezone(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        controls = await load_controls(settings.state_path)
        current = controls.get("tutor_time_zone")
        if current:
            local_now = format_datetime(
                datetime.now(timezone.utc),
                time_zone_name=current,
                fmt="%d.%m.%Y %H:%M",
            )
            await message.answer(
                "Текущий часовой пояс преподавателя: "
                f"<b>{current}</b>\n"
                f"Локальное время: <b>{local_now}</b>\n"
                "Чтобы изменить его, отправьте "
                "<code>/timezone Europe/Moscow</code>."
            )
            return

        await message.answer(
            "Часовой пояс преподавателя пока не задан.\n"
            "Установите его командой <code>/timezone Europe/Moscow</code>."
        )
        return

    requested = validate_time_zone_name(parts[1])
    if not requested:
        await message.answer(
            "Не удалось распознать часовой пояс.\n"
            "Пример: <code>/timezone Europe/Moscow</code>."
        )
        return

    await save_tutor_time_zone(
        settings.state_path,
        tutor_time_zone=requested,
        updated_by="tutor",
        reason="timezone_updated_via_telegram",
    )
    local_now = format_datetime(
        datetime.now(timezone.utc),
        time_zone_name=requested,
        fmt="%d.%m.%Y %H:%M",
    )
    await message.answer(
        "Часовой пояс преподавателя сохранён: "
        f"<b>{requested}</b>\n"
        f"Локальное время: <b>{local_now}</b>"
    )
    await audit_log(
        "tutor",
        "timezone_updated",
        actor="tutor",
        detail={"time_zone": requested, "via": "telegram"},
    )


# -- Approval inline-button callbacks -----------------------------------------

@router.callback_query(TutorBotFilter(), F.data.startswith("appr:"))
async def on_approval_callback(
    callback: CallbackQuery, role: str, settings: Settings,
) -> None:
    if role != "tutor":
        await callback.answer("Нет доступа")
        return

    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Неверный формат")
        return

    _, action, approval_id = parts

    if action == "approve":
        ok = await approval_handler.approve(approval_id, settings)
        if ok:
            await callback.answer("Одобрено и отправлено студенту")
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply("Ответ отправлен студенту.")
        else:
            await callback.answer("Не удалось одобрить (уже обработано?)")

    elif action == "reject":
        ok = await approval_handler.reject(approval_id, settings)
        if ok:
            await callback.answer("Отклонено")
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply("Черновик отклонён.")
        else:
            await callback.answer("Не удалось отклонить (уже обработано?)")

    else:
        await callback.answer("Неизвестное действие")


# -- Tutor reply-to: approval edit or escalation response ----------------------

@router.message(TutorBotFilter(), F.text, F.reply_to_message)
@router.message(TutorBotFilter(), F.photo, F.reply_to_message)
async def on_tutor_reply(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return

    replied_to_id = message.reply_to_message.message_id
    reply_text = _reply_text(message)
    logger.info(
        "Tutor reply received: reply_to_message_id=%s text=%r has_photo=%s",
        replied_to_id,
        reply_text[:200],
        bool(message.photo),
    )

    # Check if this is a reply to an approval message (edit & approve)
    approval = await approvals.find_pending_by_tutor_message(
        settings.state_path, replied_to_id,
    )
    if approval:
        logger.info(
            "Tutor reply matched approval: approval_id=%s booking=%s",
            approval["approval_id"],
            approval["booking_id"],
        )
        if not message.text:
            await message.answer("Черновик можно править только текстовым сообщением.")
            return

        direct_send = _APPROVAL_SEND_RE.match((message.text or "").strip())
        if direct_send:
            ok = await approval_handler.edit_and_approve(
                approval["approval_id"], direct_send.group(1).strip(), settings,
            )
            if ok:
                await message.answer("Ответ отправлен студенту.")
            else:
                await message.answer("Не удалось обработать (уже обработано?).")
            return

        result = await approval_handler.revise_pending_approval(
            approval["approval_id"],
            message.text,
            settings,
        )
        if not result:
            await message.answer(
                "Не удалось обновить черновик. "
                "Попробуйте ещё раз или отправьте финальный текст через /send."
            )
            return
        await message.answer("Черновик обновлён. Проверьте новый вариант выше.")
        return

    # Otherwise check escalations (existing behaviour)
    escalation = await escalations.find_pending_by_tutor_message(
        settings.state_path, replied_to_id
    )
    booking_id = escalation["booking_id"] if escalation else None
    contact_id = escalation.get("contact_id") if escalation else None
    if not escalation:
        booking_id = _extract_booking_id_from_message(message.reply_to_message)
        contact_id = _extract_contact_id_from_message(message.reply_to_message)
    if not escalation and booking_id:
        logger.info(
            "Tutor reply fallback by booking_id from message text: booking=%s",
            booking_id,
        )
        escalation = await escalations.load(settings.state_path, booking_id)
        if escalation:
            contact_id = escalation.get("contact_id")
            if escalation.get("status") != "pending":
                await message.answer(
                    "Этот вопрос уже закрыт. Ответьте на новое сообщение студента или дождитесь новой эскалации."
                )
                return
    if not escalation and contact_id is not None and not booking_id:
        pending_for_contact = await escalations.list_pending(
            settings.state_path,
            contact_id=contact_id,
        )
        if len(pending_for_contact) == 1:
            escalation = pending_for_contact[0]
            logger.info(
                "Tutor reply fallback by contact_id from message text: contact=%s escalation_id=%s",
                contact_id,
                escalation["escalation_id"],
            )

    if not booking_id and contact_id is None:
        logger.warning(
            "Tutor reply did not match pending escalation: reply_to_message_id=%s text=%r",
            replied_to_id,
            (message.reply_to_message.text or message.reply_to_message.html_text or "")[:200],
        )
        await message.answer(
            "Не удалось сопоставить ответ с активным вопросом. "
            "Ответьте реплаем на последнее сообщение с ID брони."
        )
        return  # not an escalation reply

    if escalation:
        logger.info(
            "Tutor reply matched escalation: booking=%s tutor_message_id=%s status=%s",
            booking_id,
            escalation.get("tutor_message_id"),
            escalation.get("status"),
        )
    else:
        logger.info("Tutor reply routed by booking_id without escalation state: booking=%s", booking_id)

    booking = await bookings.load(settings.state_path, booking_id) if booking_id else None
    if booking and contact_id is None:
        contact_id = booking.get("contact_id")

    contact = await contacts.load(settings.state_path, contact_id) if contact_id is not None else None
    if not booking and not contact:
        logger.warning("Tutor replied for missing booking: %s", booking_id)
        await message.answer("Не удалось найти запись студента для этого ответа.")
        return

    student_id = (booking or {}).get("telegram_user_id") or (contact or {}).get("telegram_user_id")
    if not student_id:
        logger.warning(
            "Tutor reply blocked: booking=%s contact=%s has no telegram_user_id",
            booking_id,
            contact_id,
        )
        await message.answer("Студент ещё не подключился через deeplink.")
        return

    student_bot = registry.get_student()
    if student_bot is None:
        logger.error("Student bot not available")
        await message.answer("Ошибка: не удалось отправить ответ студенту.")
        return

    attachments = None
    photo_path = None
    if message.photo:
        attachment = await _store_tutor_photo_reply(
            message,
            settings,
            booking_id=booking_id,
            contact_id=contact_id,
        )
        attachments = [attachment]
        photo_path = attachment["local_path"]

    history_text = _history_text(message)
    sent = await send_student_message(
        bot=student_bot,
        chat_id=student_id,
        text=history_text,
        booking_id=booking_id,
        contact_id=contact_id,
        settings=settings,
        source="tutor_telegram_reply",
        actor="tutor",
        photo_path=photo_path,
        caption=message.caption or None,
        attachments=attachments,
    )
    if not sent:
        logger.error("Tutor reply delivery failed: booking=%s student_id=%s", booking_id, student_id)
        await message.answer("Ошибка: не удалось отправить ответ студенту.")
        return

    if escalation and escalation.get("status") == "pending":
        await escalations.resolve_by_id(
            settings.state_path,
            escalation["escalation_id"],
            history_text,
            resolved_by="tutor",
        )
    else:
        logger.info(
            "Tutor reply delivered without pending escalation state: booking=%s status=%s",
            booking_id,
            escalation.get("status") if escalation else None,
        )
    await conversations.update_metadata(
        settings.state_path,
        booking_id=booking_id,
        contact_id=contact_id,
        escalation_state="resolved",
        escalation_reason=None,
        current_stage="tutor_reply_sent",
        status="active",
        assigned_human="tutor",
    )
    await message.answer(templates.tutor_answer_sent())
    logger.info("Tutor reply routed: booking=%s -> student=%s", booking_id, student_id)
    await audit_log(
        "escalation", "resolved",
        booking_id=booking_id,
        actor="tutor",
        detail={
            "via": "telegram",
            "matched_pending_escalation": bool(
                escalation and escalation.get("status") == "pending"
            ),
        },
    )


@router.message(TutorBotFilter(), F.text)
async def on_tutor_message(message: Message, role: str, settings: Settings) -> None:
    if role != "tutor":
        return
    if message.reply_to_message:
        return
    if (message.text or "").startswith("/"):
        return

    if await _maybe_export_dialogue(message, settings):
        return

    knowledge = await knowledge_search(settings.knowledge_path, message.text, limit=5)
    client = OpenclawClient(settings)
    reply = await client.tutor_assistant(message.text, knowledge=knowledge)
    await message.answer(reply, disable_web_page_preview=True)


def _extract_booking_id_from_message(message: Message | None) -> str | None:
    if message is None:
        return None
    candidates = [
        getattr(message, "html_text", None),
        getattr(message, "text", None),
        getattr(message, "caption", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        match = _BOOKING_ID_RE.search(candidate)
        if match:
            return match.group(1)
    return None


def _extract_contact_id_from_message(message: Message | None) -> int | None:
    if message is None:
        return None
    candidates = [
        getattr(message, "html_text", None),
        getattr(message, "text", None),
        getattr(message, "caption", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        match = _CONTACT_ID_RE.search(candidate)
        if match:
            return int(match.group(1))
    return None


def _reply_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.photo:
        return "[image]"
    return ""


def _history_text(message: Message) -> str:
    if message.photo:
        caption = (message.caption or "").strip()
        return f"[image] {caption}" if caption else "[image]"
    return message.text or ""


async def _store_tutor_photo_reply(
    message: Message,
    settings: Settings,
    *,
    booking_id: str | None,
    contact_id: int | None,
) -> dict[str, str | None]:
    photo: PhotoSize = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    upload_scope = booking_id or f"contact-{contact_id or 'unknown'}"
    upload_dir = Path(settings.uploads_path) / upload_scope / "tutor-replies"
    upload_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(upload_dir / f"{photo.file_id}.jpg")
    await message.bot.download_file(file.file_path, destination=local_path)
    return {
        "file_id": photo.file_id,
        "file_type": "photo",
        "mime_type": "image/jpeg",
        "local_path": local_path,
        "caption": message.caption or None,
    }


async def _maybe_export_dialogue(
    message: Message,
    settings: Settings,
    *,
    request_text: str | None = None,
    force: bool = False,
) -> bool:
    text = (request_text or message.text or "").strip()
    if not force and not _looks_like_dialog_export_request(text):
        return False
    tutor_time_zone = None
    try:
        controls = await load_controls(settings.state_path)
    except Exception:
        logger.warning("Could not load tutor time zone for transcript export", exc_info=True)
    else:
        tutor_time_zone = controls.get("tutor_time_zone")

    booking_id = _extract_booking_id_from_text(text)
    if booking_id:
        booking = await bookings.load(settings.state_path, booking_id)
        if not booking:
            await message.answer(f"Не удалось найти бронь {booking_id}.")
            return True
        chat = await conversations.load_chat(settings.state_path, booking_id)
        contact = None
        contact_id = booking.get("contact_id")
        if contact_id is not None:
            contact = await contacts.load(settings.state_path, contact_id)
        transcript = _render_transcript(
            messages=chat.messages,
            contact=contact,
            booking=booking,
            title=f"Полный диалог по брони {booking_id}",
            viewer_time_zone=tutor_time_zone,
        )
        await _send_export_chunks(message, transcript)
        await audit_log(
            "tutor",
            "dialogue_exported",
            booking_id=booking_id,
            actor="tutor",
            detail={
                "contact_id": contact_id,
                "message_count": len(chat.messages),
                "scope": "booking",
            },
        )
        logger.info("Tutor dialogue export sent: booking=%s messages=%s", booking_id, len(chat.messages))
        return True

    target = request_text.strip() if force and request_text else _extract_dialog_export_target(text)
    if not target:
        await message.answer(
            "Укажите, чей диалог выгрузить: username, e-mail, имя контакта или booking_id."
        )
        return True

    exact_contact = await contacts.load_by_telegram_username(settings.state_path, target)
    matches = [exact_contact] if exact_contact else await contacts.search_dialog_targets(
        settings.state_path,
        target,
        limit=5,
    )
    if not matches:
        await message.answer(f"Не удалось найти диалог для {target}.")
        return True

    contact = _select_dialog_target(matches, target)
    if contact is None:
        variants = "\n".join(_format_target_option(item) for item in matches[:5])
        await message.answer(
            "Найдено несколько подходящих диалогов. Уточните booking_id, username или e-mail:\n"
            f"{variants}"
        )
        return True

    contact_id = contact["id"]
    active_booking = await bookings.resolve_context_for_contact(settings.state_path, contact_id)
    chat = await conversations.load_chat_by_contact(
        settings.state_path,
        contact_id,
        booking_id=active_booking["booking_id"] if active_booking else None,
    )
    transcript = _render_transcript(
        messages=chat.messages,
        contact=contact,
        booking=active_booking,
        title=f"Полный диалог с {contact.get('telegram_username') or contact.get('name') or contact_id}",
        viewer_time_zone=tutor_time_zone,
    )
    await _send_export_chunks(message, transcript)
    await audit_log(
        "tutor",
        "dialogue_exported",
        actor="tutor",
        detail={
            "contact_id": contact_id,
            "booking_id": active_booking["booking_id"] if active_booking else None,
            "message_count": len(chat.messages),
            "scope": "contact",
            "query": target,
        },
    )
    logger.info("Tutor dialogue export sent: contact=%s query=%r messages=%s", contact_id, target, len(chat.messages))
    return True


def _looks_like_dialog_export_request(text: str) -> bool:
    return bool(
        _DIALOG_EXPORT_INTENT_RE.search(text)
        or (_DIALOG_EXPORT_VERB_RE.search(text) and _DIALOG_EXPORT_NOUN_RE.search(text))
    )


def _extract_booking_id_from_text(text: str) -> str | None:
    labelled = re.search(
        r"(?:\bbooking_id\b|\bID\s+брони\b|\bбронь\b)\s*[:#]?\s*([A-Za-z0-9_-]{8,})",
        text,
        re.IGNORECASE,
    )
    if labelled:
        return labelled.group(1)
    stripped = text.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{12,}", stripped):
        return stripped
    return None


def _extract_dialog_export_target(text: str) -> str | None:
    match = _DIALOG_EXPORT_TARGET_RE.search(text)
    if not match:
        return None

    target = " ".join(match.group("target").split())
    target = _EXPORT_TRAILING_RE.sub("", target).strip(" \n\r\t.,:;!?\"'«»()[]")
    return target or None


def _select_dialog_target(matches: list[dict], raw_target: str) -> dict | None:
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    normalized = raw_target.strip().lstrip("@").lower()
    exact = []
    for item in matches:
        if (item.get("telegram_username") or "").lower() == normalized:
            exact.append(item)
        elif (item.get("email") or "").lower() == normalized:
            exact.append(item)
        elif (item.get("name") or "").lower() == normalized:
            exact.append(item)
    if len(exact) == 1:
        return exact[0]
    return None


def _format_target_option(contact: dict) -> str:
    name = contact.get("name") or "без имени"
    username = contact.get("telegram_username")
    email = contact.get("email")
    parts = [name]
    if username:
        parts.append(f"telegram: {username}")
    if email:
        parts.append(f"email: {email}")
    return "• " + " | ".join(parts)


def _render_transcript(
    *,
    messages: list[dict],
    contact: dict | None,
    booking: dict | None,
    title: str,
    viewer_time_zone: str | None = None,
) -> str:
    lines = [title]

    if contact:
        name = contact.get("name")
        username = contact.get("telegram_username")
        email = contact.get("email")
        if name:
            lines.append(f"Контакт: {name}")
        if username:
            lines.append(f"Telegram: {username}")
        if email:
            lines.append(f"Email: {email}")

    if booking:
        lines.append(f"ID брони: {booking.get('booking_id')}")
        if booking.get("title"):
            lines.append(f"Занятие: {booking['title']}")
        if booking.get("start_time"):
            lines.append(
                "Время занятия: "
                + format_datetime(
                    booking.get("start_time"),
                    time_zone_name=viewer_time_zone,
                    fmt="%d.%m.%Y %H:%M",
                )
            )

    if viewer_time_zone:
        lines.append(f"Время сообщений: {viewer_time_zone}")

    lines.append("")
    lines.append("История:")

    if not messages:
        lines.append("Диалог пуст.")
        return "\n".join(lines)

    for entry in messages:
        timestamp = _format_message_timestamp(entry.get("ts"), viewer_time_zone)
        speaker = _speaker_label(entry)
        content = (entry.get("content") or "").strip() or "[пустое сообщение]"
        lines.append(f"[{timestamp}] {speaker}: {content}")

    return "\n".join(lines)


def _format_message_timestamp(value: str | None, time_zone_name: str | None) -> str:
    if not value:
        return "без времени"
    dt = parse_datetime(value)
    if dt is None:
        return value
    return format_datetime(
        dt,
        time_zone_name=time_zone_name,
        fmt="%d.%m.%Y %H:%M",
        fallback=value,
    )


def _speaker_label(entry: dict) -> str:
    role = entry.get("role")
    source = entry.get("source") or ""
    if role == "user":
        return "Студент"
    if source in _DIRECT_TUTOR_SOURCES:
        return "Преподаватель"
    if source in _REVIEWED_TUTOR_SOURCES:
        return "Бот (одобрено преподавателем)"
    if role == "assistant":
        return "Бот"
    return "Система"


async def _send_export_chunks(message: Message, text: str, *, limit: int = 3500) -> None:
    chunks = _chunk_text(text, limit=limit)
    for chunk in chunks:
        await message.answer(chunk)


def _chunk_text(text: str, *, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start:start + limit])
            continue
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
            continue
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))
    return chunks
