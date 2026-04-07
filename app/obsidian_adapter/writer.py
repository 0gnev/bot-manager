"""
Obsidian vault writer.

Exports booking and conversation state as markdown files into data/knowledge/,
making them viewable and editable in Obsidian.

Layout:
  data/knowledge/
    bookings/
      {booking_id}.md      — booking card with YAML frontmatter
    conversations/
      {booking_id|contact-<contact_id>}.md      — conversation log
    escalations/
      {booking_id|contact-<contact_id>}-{escalation_id}.md      — escalation record
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return value


def _scope_doc_id(booking_id: str | None, contact_id: int | None) -> str:
    if booking_id:
        return booking_id
    if contact_id is not None:
        return f"contact-{contact_id}"
    return "unknown"


def _contact_label(contact_id: int | None, contact: dict | None = None) -> str:
    if contact:
        return (
            contact.get("name")
            or contact.get("telegram_username")
            or contact.get("email")
            or (f"Контакт {contact_id}" if contact_id is not None else "Контакт")
        )
    if contact_id is not None:
        return f"Контакт {contact_id}"
    return "Контакт"


# ── Booking ──────────────────────────────────────────────────────────────────


async def export_booking(knowledge_path: str, booking: dict) -> None:
    """Write a booking as an Obsidian-friendly markdown note."""
    booking_id = booking.get("booking_id", "unknown")
    out_dir = Path(knowledge_path) / "bookings"
    _ensure_dir(out_dir)

    attendee = booking.get("attendee") or {}
    organizer = booking.get("organizer") or {}
    location = booking.get("location") or {}

    status = booking.get("status", "unknown")
    status_emoji = {"active": "🟢", "cancelled": "🔴"}.get(status, "⚪")

    lines = [
        "---",
        f"booking_id: {booking_id}",
        f"contact_id: {booking.get('contact_id') or ''}",
        f"status: {status}",
        f"event: {booking.get('event', '')}",
        f"start_time: {booking.get('start_time', '')}",
        f"end_time: {booking.get('end_time', '')}",
        f"telegram_user_id: {booking.get('telegram_user_id') or ''}",
        f"updated_at: {booking.get('updated_at') or ''}",
        "---",
        "",
        f"# {booking.get('title', 'Booking')}",
        "",
        f"**Статус:** {status_emoji} {status}",
        "",
        "## Участники",
        "",
        f"- **Студент:** {attendee.get('name', '—')}",
        f"  - Email: {attendee.get('email', '—')}",
        f"  - Телефон: {attendee.get('phone') or '—'}",
        f"  - Telegram: {attendee.get('telegram') or '—'}",
        f"  - Часовой пояс: {attendee.get('timeZone', '—')}",
        f"- **Организатор:** {organizer.get('name', '—')}",
        f"  - Email: {organizer.get('email', '—')}",
        "",
        "## Расписание",
        "",
        f"- **Начало:** {_fmt_dt(booking.get('start_time'))}",
        f"- **Конец:** {_fmt_dt(booking.get('end_time'))}",
        "",
    ]

    meeting_url = booking.get("meeting_url")
    if meeting_url:
        loc_name = location.get("name", "Ссылка")
        lines += [
            "## Подключение",
            "",
            f"- **Платформа:** {loc_name}",
            f"- **Ссылка:** [{meeting_url}]({meeting_url})",
            "",
        ]

    custom_inputs = booking.get("custom_inputs") or []
    if custom_inputs:
        lines += ["## Дополнительные поля", ""]
        for inp in custom_inputs:
            label = inp.get("label", inp.get("name", "—"))
            value = inp.get("value", "—")
            lines.append(f"- **{label}:** {value}")
        lines.append("")

    telegram_uid = booking.get("telegram_user_id")
    if telegram_uid:
        lines += [
            "## Telegram",
            "",
            f"- **User ID:** `{telegram_uid}`",
            "",
        ]

    content = "\n".join(lines)
    path = out_dir / f"{booking_id}.md"
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


# ── Conversation ─────────────────────────────────────────────────────────────


async def export_conversation(
    knowledge_path: str,
    history: list[dict],
    *,
    booking_id: str | None = None,
    contact_id: int | None = None,
    booking: dict | None = None,
    contact: dict | None = None,
) -> None:
    """Write conversation history as an Obsidian markdown note."""
    out_dir = Path(knowledge_path) / "conversations"
    _ensure_dir(out_dir)
    doc_id = _scope_doc_id(booking_id, contact_id)

    title = "Диалог"
    if booking:
        attendee = booking.get("attendee") or {}
        student = attendee.get("name", "")
        event = booking.get("title", "")
        if student or event:
            title = f"Диалог — {student or event}"
    elif contact_id is not None:
        title = f"Диалог — {_contact_label(contact_id, contact)}"

    lines = [
        "---",
        f"booking_id: {booking_id or ''}",
        f"contact_id: {contact_id if contact_id is not None else ''}",
        f"messages: {len(history)}",
        "---",
        "",
        f"# {title}",
        "",
    ]

    role_labels = {"user": "🧑 Студент", "assistant": "🤖 Бот"}

    for msg in history:
        role = role_labels.get(msg.get("role", ""), msg.get("role", "?"))
        ts = _fmt_dt(msg.get("ts"))
        content = msg.get("content", "")
        lines += [
            f"### {role}  <small>{ts}</small>",
            "",
            content,
            "",
        ]

    content = "\n".join(lines)
    path = out_dir / f"{doc_id}.md"
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


# ── Escalation ───────────────────────────────────────────────────────────────


async def export_escalation(
    knowledge_path: str,
    escalation: dict,
    *,
    booking: dict | None = None,
    contact: dict | None = None,
) -> None:
    """Write escalation record as an Obsidian markdown note."""
    out_dir = Path(knowledge_path) / "escalations"
    _ensure_dir(out_dir)

    booking_id = escalation.get("booking_id")
    contact_id = escalation.get("contact_id")
    doc_id = _scope_doc_id(booking_id, contact_id)
    escalation_id = escalation.get("escalation_id")
    status = escalation.get("status", "unknown")
    status_emoji = {"pending": "⏳", "resolved": "✅"}.get(status, "⚪")
    title_context = (
        booking.get("title")
        if booking
        else _contact_label(contact_id, contact)
    )

    lines = [
        "---",
        f"booking_id: {booking_id or ''}",
        f"contact_id: {contact_id if contact_id is not None else ''}",
        f"escalation_id: {escalation_id or ''}",
        f"status: {status}",
        f"created_at: {escalation.get('created_at') or ''}",
        f"resolved_at: {escalation.get('resolved_at') or ''}",
        "---",
        "",
        f"# Эскалация — {title_context or doc_id}{f' / {escalation_id}' if escalation_id else ''}",
        "",
        f"**Статус:** {status_emoji} {status}",
        "",
        "## Вопрос студента",
        "",
        escalation.get("question", "—"),
        "",
    ]

    summary = escalation.get("summary")
    if summary:
        lines += [
            "## Сводка",
            "",
            summary,
            "",
        ]

    relevant_history = escalation.get("relevant_history") or []
    if relevant_history:
        role_labels = {"user": "Студент", "assistant": "Бот", "system": "Система"}
        lines += [
            "## Недавний диалог",
            "",
        ]
        for item in relevant_history:
            role = role_labels.get(item.get("role", ""), item.get("role", "Сообщение"))
            lines += [
                f"### {role}",
                "",
                item.get("content", "—"),
                "",
            ]

    draft_reply = escalation.get("draft_reply")
    if draft_reply:
        lines += [
            "## Черновик ответа",
            "",
            draft_reply,
            "",
        ]

    tutor_reply = escalation.get("tutor_reply")
    if tutor_reply:
        lines += [
            "## Ответ преподавателя",
            "",
            tutor_reply,
            "",
        ]

    content = "\n".join(lines)
    filename = f"{doc_id}-{escalation_id}.md" if escalation_id else f"{doc_id}.md"
    path = out_dir / filename
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
