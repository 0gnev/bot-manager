"""
Message templates for student and tutor conversations.
All messages use HTML parse mode.
"""

from __future__ import annotations

from datetime import datetime
from html import escape


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%d %b %Y, %H:%M")


# -- Student messages ----------------------------------------------------------

def welcome(student_name: str, event_title: str, start_time: datetime | None) -> str:
    return render_prompt(
        "welcome",
        student_name=student_name,
        event_title=event_title,
        start_time=fmt_dt(start_time),
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


# -- Tutor (escalation) messages -----------------------------------------------

def escalation_notice(
    student_name: str,
    booking_id: str,
    question: str,
    event_title: str,
    start_time: datetime | None,
    student_email: str | None = None,
    student_phone: str | None = None,
    student_telegram: str | None = None,
    student_time_zone: str | None = None,
    student_telegram_user_id: int | None = None,
) -> str:
    return tutor_notice(
        header="Новое сообщение от студента",
        student_name=student_name,
        booking_id=booking_id,
        question=question,
        event_title=event_title,
        start_time=start_time,
        student_email=student_email,
        student_phone=student_phone,
        student_telegram=student_telegram,
        student_time_zone=student_time_zone,
        student_telegram_user_id=student_telegram_user_id,
    )


def manual_escalation_notice(
    *,
    context_label: str,
    student_name: str,
    booking_id: str,
    question: str,
    event_title: str,
    start_time: datetime | None,
    student_email: str | None = None,
    student_phone: str | None = None,
    student_telegram: str | None = None,
    student_time_zone: str | None = None,
    student_telegram_user_id: int | None = None,
) -> str:
    return tutor_notice(
        header=f"Сообщение от студента ({context_label})",
        student_name=student_name,
        booking_id=booking_id,
        question=question,
        event_title=event_title,
        start_time=start_time,
        student_email=student_email,
        student_phone=student_phone,
        student_telegram=student_telegram,
        student_time_zone=student_time_zone,
        student_telegram_user_id=student_telegram_user_id,
    )


def tutor_notice(
    *,
    header: str,
    student_name: str,
    booking_id: str,
    question: str,
    event_title: str,
    start_time: datetime | None,
    student_email: str | None = None,
    student_phone: str | None = None,
    student_telegram: str | None = None,
    student_time_zone: str | None = None,
    student_telegram_user_id: int | None = None,
) -> str:
    lines = [
        f"<b>{escape(header)}</b>",
        "",
        f"<b>Студент:</b> {escape(student_name or 'Студент')}",
    ]

    if student_telegram:
        lines.append(f"<b>Telegram:</b> {escape(student_telegram)}")
    if student_phone:
        lines.append(f"<b>Телефон:</b> {escape(student_phone)}")
    if student_email:
        lines.append(f"<b>Email:</b> {escape(student_email)}")
    if student_time_zone:
        lines.append(f"<b>Часовой пояс:</b> {escape(student_time_zone)}")
    if student_telegram_user_id:
        lines.append(f"<b>Telegram user ID:</b> <code>{student_telegram_user_id}</code>")

    lines.extend(
        [
            f"<b>Занятие:</b> {escape(event_title or 'Занятие')} ({escape(fmt_dt(start_time))})",
            f"<b>ID брони:</b> <code>{escape(booking_id)}</code>",
            "",
            "<b>Сообщение студента:</b>",
            escape(question or "—"),
            "",
            "<i>Ответьте на это сообщение в Telegram.</i>",
        ]
    )
    return "\n".join(lines)


def tutor_answer_sent() -> str:
    return "Ответ отправлен студенту."
