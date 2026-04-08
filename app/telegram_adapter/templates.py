"""
Message templates for student and tutor conversations.
All messages use HTML parse mode.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from bridge.prompts.loader import render_prompt
from bridge.timezones import format_datetime, validate_time_zone_name


def fmt_dt(
    dt: datetime | None,
    *,
    time_zone_name: str | None = None,
    include_time_zone: bool = False,
) -> str:
    return format_datetime(
        dt,
        time_zone_name=time_zone_name,
        include_time_zone=include_time_zone,
    )


def _time_zone_line(
    time_zone_name: str | None,
    *,
    label: str = "Часовой пояс",
) -> str:
    normalized = validate_time_zone_name(time_zone_name)
    if not normalized:
        return ""
    return f"{label}: <b>{escape(normalized)}</b>"


# -- Student messages ----------------------------------------------------------

def welcome(
    student_name: str,
    event_title: str,
    start_time: datetime | None,
    *,
    time_zone_name: str | None = None,
) -> str:
    return render_prompt(
        "welcome",
        student_name=student_name,
        event_title=event_title,
        start_time=fmt_dt(start_time, time_zone_name=time_zone_name),
        time_zone_line=_time_zone_line(time_zone_name),
    )


def session_details(
    event_title: str,
    start_time: datetime | None,
    end_time: datetime | None,
    meeting_url: str | None,
    *,
    time_zone_name: str | None = None,
) -> str:
    lines = [
        f"<b>{event_title}</b>",
        f"Начало: {fmt_dt(start_time, time_zone_name=time_zone_name)}",
        f"Конец:  {fmt_dt(end_time, time_zone_name=time_zone_name)}",
    ]
    time_zone_line = _time_zone_line(time_zone_name)
    if time_zone_line:
        lines.append(time_zone_line)
    if meeting_url:
        lines.append(f'Ссылка: <a href="{meeting_url}">Подключиться</a>')
    return "\n".join(lines)


def booking_not_found() -> str:
    return (
        "Не удалось найти вашу запись. "
        "Убедитесь, что вы перешли по ссылке из подтверждения бронирования."
    )


def contact_only_welcome() -> str:
    return (
        "Привет! Чем могу помочь?\n\n"
        "Если вы записывались через Planerka, убедитесь, что ваш Telegram username "
        "совпадает с тем, который вы указали при бронировании."
    )


def booking_link_ambiguous() -> str:
    return (
        "Не удалось автоматически определить вашу запись по Telegram username. "
        "Напишите преподавателю, и он вручную поможет с привязкой."
    )


def booking_linked_elsewhere() -> str:
    return (
        "Эта запись уже привязана к другому Telegram-аккаунту. "
        "Если это ошибка, напишите преподавателю."
    )


def out_of_scope_question() -> str:
    return (
        "Я помогаю только по вопросам занятий, записи и подготовки к ЕГЭ по информатике. "
        "Если вопрос связан с занятием, уточните его, пожалуйста."
    )


def multiple_bookings_found(bookings: list[dict]) -> str:
    lines = [
        "У вас несколько активных записей.",
        "Не удалось однозначно выбрать нужную запись автоматически.",
        "Если это мешает, напишите преподавателю, и он поможет переключить контекст.",
        "",
        "<b>Доступные записи:</b>",
    ]
    for index, booking in enumerate(bookings, start=1):
        lines.append(
            f"{index}. {escape(booking.get('title', 'Занятие'))} "
            f"— {escape(booking.get('start_time_label', '—'))}"
        )
    return "\n".join(lines)


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
    *,
    time_zone_name: str | None = None,
) -> str:
    lines = [
        "⏰ <b>Время занятия изменилось</b>\n",
        f"<b>{event_title}</b>",
        f"Новое время: {fmt_dt(start_time, time_zone_name=time_zone_name)} — {fmt_dt(end_time, time_zone_name=time_zone_name)}",
    ]
    time_zone_line = _time_zone_line(time_zone_name)
    if time_zone_line:
        lines.append(time_zone_line)
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
    booking_id: str | None,
    question: str,
    event_title: str | None,
    start_time: datetime | None,
    viewer_time_zone: str | None = None,
    student_email: str | None = None,
    student_phone: str | None = None,
    student_telegram: str | None = None,
    student_time_zone: str | None = None,
    student_telegram_user_id: int | None = None,
    summary: str | None = None,
    relevant_history: list[dict] | None = None,
    draft_reply: str | None = None,
) -> str:
    return tutor_notice(
        header="Новое сообщение от студента",
        student_name=student_name,
        booking_id=booking_id,
        question=question,
        event_title=event_title,
        start_time=start_time,
        viewer_time_zone=viewer_time_zone,
        student_email=student_email,
        student_phone=student_phone,
        student_telegram=student_telegram,
        student_time_zone=student_time_zone,
        student_telegram_user_id=student_telegram_user_id,
        summary=summary,
        relevant_history=relevant_history,
        draft_reply=draft_reply,
    )


def manual_escalation_notice(
    *,
    context_label: str,
    student_name: str,
    booking_id: str | None,
    question: str,
    event_title: str | None,
    start_time: datetime | None,
    viewer_time_zone: str | None = None,
    student_email: str | None = None,
    student_phone: str | None = None,
    student_telegram: str | None = None,
    student_time_zone: str | None = None,
    student_telegram_user_id: int | None = None,
    summary: str | None = None,
    relevant_history: list[dict] | None = None,
    draft_reply: str | None = None,
) -> str:
    return tutor_notice(
        header=f"Сообщение от студента ({context_label})",
        student_name=student_name,
        booking_id=booking_id,
        question=question,
        event_title=event_title,
        start_time=start_time,
        viewer_time_zone=viewer_time_zone,
        student_email=student_email,
        student_phone=student_phone,
        student_telegram=student_telegram,
        student_time_zone=student_time_zone,
        student_telegram_user_id=student_telegram_user_id,
        summary=summary,
        relevant_history=relevant_history,
        draft_reply=draft_reply,
    )


def tutor_notice(
    *,
    header: str,
    student_name: str,
    booking_id: str | None,
    question: str,
    event_title: str | None,
    start_time: datetime | None,
    viewer_time_zone: str | None = None,
    student_email: str | None = None,
    student_phone: str | None = None,
    student_telegram: str | None = None,
    student_time_zone: str | None = None,
    student_telegram_user_id: int | None = None,
    summary: str | None = None,
    relevant_history: list[dict] | None = None,
    draft_reply: str | None = None,
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
    if viewer_time_zone:
        lines.append(f"<b>Показано в часовом поясе:</b> {escape(viewer_time_zone)}")
    if student_telegram_user_id:
        lines.append(f"<b>Telegram user ID:</b> <code>{student_telegram_user_id}</code>")

    if booking_id:
        lines.append(
            f"<b>Занятие:</b> {escape(event_title or 'Занятие')} — {escape(fmt_dt(start_time, time_zone_name=viewer_time_zone))}"
        )
        lines.append(f"<b>ID брони:</b> <code>{escape(booking_id)}</code>")
    else:
        lines.append("<b>Контекст:</b> Общий вопрос без привязки к записи")

    lines.extend(
        [
            "",
            "<b>Сообщение студента:</b>",
            escape(question or "—"),
        ]
    )
    if summary:
        lines.extend(["", "<b>Сводка:</b>", escape(summary)])
    if relevant_history:
        lines.extend(["", "<b>Недавний диалог:</b>"])
        for item in relevant_history[-4:]:
            role_label = _history_role_label(item.get("role"))
            lines.append(
                f"<b>{escape(role_label)}:</b> {escape(item.get('content') or '—')}"
            )
    if draft_reply:
        lines.extend(["", "<b>Черновик ответа:</b>", escape(draft_reply)])
    lines.extend(["", "<i>Ответьте на это сообщение в Telegram.</i>"])
    return "\n".join(lines)


def knowledge_suggestion_notice(
    suggestion: dict,
    *,
    booking: dict | None = None,
    contact: dict | None = None,
) -> str:
    student_name = ((booking or {}).get("attendee") or {}).get("name") or (contact or {}).get("name") or "Студент"
    lines = [
        "<b>Предложение для базы знаний</b>",
        f"<b>Заголовок:</b> {escape(suggestion.get('title') or '—')}",
        f"<b>Источник:</b> {escape(suggestion.get('source_kind') or '—')}",
        f"<b>Студент:</b> {escape(student_name)}",
    ]
    if suggestion.get("booking_id"):
        lines.append(f"<b>ID брони:</b> <code>{escape(suggestion['booking_id'])}</code>")
    elif suggestion.get("contact_id") is not None:
        lines.append(f"<b>ID контакта:</b> <code>{suggestion['contact_id']}</code>")
    if suggestion.get("source_question"):
        lines.extend(["", "<b>Исходный вопрос:</b>", escape(suggestion["source_question"])])
    lines.extend(["", "<b>Финальный ответ:</b>", escape(suggestion.get("answer_text") or "—")])
    rationale = suggestion.get("rationale")
    if rationale:
        lines.extend(["", "<b>Почему стоит сохранить:</b>", escape(rationale)])
    lines.extend(
        [
            "",
            "<b>Черновик знания:</b>",
            escape(suggestion.get("content_markdown") or "—"),
            "",
            "<i>Сохранить это как новое знание для будущих диалогов?</i>",
        ]
    )
    return "\n".join(lines)


def tutor_answer_sent() -> str:
    return "Ответ отправлен студенту."


def _history_role_label(role: str | None) -> str:
    return {
        "user": "Студент",
        "assistant": "Бот",
        "system": "Система",
    }.get(role or "", role or "Сообщение")
