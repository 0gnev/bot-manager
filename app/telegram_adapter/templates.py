"""
Message templates for student and tutor conversations.
All messages use HTML parse mode.
"""

from __future__ import annotations

from datetime import datetime


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%d %b %Y, %H:%M")


# ── Student messages ──────────────────────────────────────────────────────────

def welcome(student_name: str, event_title: str, start_time: datetime | None) -> str:
    return (
        f"Привет, <b>{student_name}</b>! 👋\n\n"
        f"Ваша запись подтверждена: <b>{event_title}</b>\n"
        f"Дата и время: <b>{fmt_dt(start_time)}</b>\n\n"
        "Здесь вы можете задать вопросы и получить всё необходимое перед занятием."
    )


def session_details(
    event_title: str,
    start_time: datetime | None,
    end_time: datetime | None,
    meeting_url: str | None,
) -> str:
    lines = [
        f"<b>{event_title}</b>",
        f"Начало: {fmt_dt(start_time)}",
        f"Конец:  {fmt_dt(end_time)}",
    ]
    if meeting_url:
        lines.append(f'Ссылка: <a href="{meeting_url}">Подключиться</a>')
    return "\n".join(lines)


def booking_not_found() -> str:
    return (
        "Не удалось найти вашу запись. "
        "Убедитесь, что вы перешли по ссылке из подтверждения бронирования."
    )


def already_linked() -> str:
    return "Ваша запись уже привязана. Чем могу помочь?"


def answer(text: str) -> str:
    return text


def clarify(text: str) -> str:
    return text


def escalated_to_tutor() -> str:
    return (
        "Я передал ваш вопрос преподавателю — он ответит в ближайшее время."
    )


def booking_rescheduled(
    event_title: str,
    start_time: datetime | None,
    end_time: datetime | None,
    meeting_url: str | None,
) -> str:
    lines = [
        "⏰ <b>Время занятия изменилось</b>\n",
        f"<b>{event_title}</b>",
        f"Новое время: {fmt_dt(start_time)} — {fmt_dt(end_time)}",
    ]
    if meeting_url:
        lines.append(f'Ссылка: <a href="{meeting_url}">Подключиться</a>')
    return "\n".join(lines)


def booking_cancelled(event_title: str) -> str:
    return (
        f"❌ <b>Занятие отменено</b>\n\n"
        f"Занятие «{event_title}» было отменено. "
        "Если у вас есть вопросы — напишите преподавателю."
    )


def image_received() -> str:
    return "Изображение получено, обрабатываю…"


# ── Tutor (escalation) messages ───────────────────────────────────────────────

def escalation_notice(
    student_name: str,
    booking_id: str,
    question: str,
    event_title: str,
    start_time: datetime | None,
) -> str:
    return (
        f"<b>Вопрос от студента</b>\n"
        f"Студент: {student_name}\n"
        f"Занятие: {event_title} ({fmt_dt(start_time)})\n"
        f"ID брони: <code>{booking_id}</code>\n\n"
        f"{question}\n\n"
        "<i>Чтобы ответить, отправьте команду:</i>\n"
        f"<code>/reply {booking_id} Ваш ответ</code>"
    )


def tutor_answer_sent() -> str:
    return "Ответ отправлен студенту."
