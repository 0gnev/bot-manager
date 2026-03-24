"""
Obsidian vault writer.

Exports booking and conversation state as markdown files into data/knowledge/,
making them viewable and editable in Obsidian.

Layout:
  data/knowledge/
    bookings/
      {booking_id}.md      — booking card with YAML frontmatter
    conversations/
      {booking_id}.md      — conversation log
    escalations/
      {booking_id}.md      — escalation record
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
        f"status: {status}",
        f"event: {booking.get('event', '')}",
        f"start_time: {booking.get('start_time', '')}",
        f"end_time: {booking.get('end_time', '')}",
        f"telegram_user_id: {booking.get('telegram_user_id', '')}",
        f"updated_at: {booking.get('updated_at', '')}",
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
    knowledge_path: str, booking_id: str, history: list[dict], booking: dict | None = None
) -> None:
    """Write conversation history as an Obsidian markdown note."""
    out_dir = Path(knowledge_path) / "conversations"
    _ensure_dir(out_dir)

    title = "Диалог"
    if booking:
        attendee = booking.get("attendee") or {}
        student = attendee.get("name", "")
        event = booking.get("title", "")
        if student or event:
            title = f"Диалог — {student or event}"

    lines = [
        "---",
        f"booking_id: {booking_id}",
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
    path = out_dir / f"{booking_id}.md"
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


# ── Escalation ───────────────────────────────────────────────────────────────


async def export_escalation(knowledge_path: str, escalation: dict) -> None:
    """Write escalation record as an Obsidian markdown note."""
    out_dir = Path(knowledge_path) / "escalations"
    _ensure_dir(out_dir)

    booking_id = escalation.get("booking_id", "unknown")
    status = escalation.get("status", "unknown")
    status_emoji = {"pending": "⏳", "resolved": "✅"}.get(status, "⚪")

    lines = [
        "---",
        f"booking_id: {booking_id}",
        f"status: {status}",
        f"created_at: {escalation.get('created_at', '')}",
        f"resolved_at: {escalation.get('resolved_at', '')}",
        "---",
        "",
        f"# Эскалация — {booking_id}",
        "",
        f"**Статус:** {status_emoji} {status}",
        "",
        "## Вопрос студента",
        "",
        escalation.get("question", "—"),
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
    path = out_dir / f"{booking_id}.md"
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
