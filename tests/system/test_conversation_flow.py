from __future__ import annotations

from pathlib import Path

import pytest

from tests.system.harness import build_system_harness


@pytest.fixture()
def system_harness(db_clean, database_url, tmp_path: Path):
    harness, resources = build_system_harness(
        database_url=database_url,
        tmp_path=tmp_path,
    )
    try:
        yield harness
    finally:
        for resource in resources:
            resource.stop()


def _booking_payload(booking_id: str) -> dict:
    return {
        "event": "BOOKING_CREATED",
        "uid": booking_id,
        "title": "Пробный урок",
        "description": "Созвон на 30 минут",
        "startTime": "2026-04-06T12:30:00+06:00",
        "endTime": "2026-04-06T13:00:00+06:00",
        "organizer": {
            "name": "Преподаватель",
            "email": "tutor@example.com",
            "timeZone": "Asia/Bishkek",
        },
        "attendees": [
            {
                "name": "Тестовый Ученик",
                "email": "student@example.com",
                "telegram": "@student",
                "timeZone": "Asia/Bishkek",
            }
        ],
        "location": {
            "name": "Google Meet",
            "url": "https://meet.example.com/test",
        },
    }


def _booking_payload_with_overrides(
    booking_id: str,
    *,
    title: str,
    start_time: str,
    end_time: str,
) -> dict:
    payload = _booking_payload(booking_id)
    payload["title"] = title
    payload["startTime"] = start_time
    payload["endTime"] = end_time
    return payload


def test_full_conversation_flow(system_harness) -> None:
    booking_id = "booking-system-flow"
    webhook = system_harness.post_planerka_webhook(_booking_payload(booking_id))
    assert webhook == {"ok": True}

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text(f"/start {booking_id}")
    system_harness.telegram.wait_for_sent_count(system_harness.student_token, student_before + 2)
    start_messages = system_harness.telegram.sent_since(system_harness.student_token, student_before)
    assert any("Привет" in (item.get("text") or "") for item in start_messages)
    assert any("Пробный урок" in (item.get("text") or "") for item in start_messages)

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text("Когда занятие и где ссылка?")
    standard_reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Ссылка указана",
    )
    assert "Занятие в запланированное время" in standard_reply["text"]

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    tutor_before = system_harness.telegram.sent_count(system_harness.owner_token)
    system_harness.send_student_text("У меня нестандартный вопрос по индивидуальному плану")

    tutor_notice = system_harness.telegram.wait_for_sent_message(
        system_harness.owner_token,
        after_index=tutor_before,
        chat_id=system_harness.tutor_chat_id,
        contains="нестандартный вопрос",
    )
    student_notice = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="передал",
    )
    assert "ID брони" in tutor_notice["text"]
    assert "преподавателю" in student_notice["text"]

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_tutor_text(
        "Это нестандартный случай, обсудим его на созвоне.",
        reply_to_message=tutor_notice,
    )
    tutor_reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="обсудим его на созвоне",
    )
    assert tutor_reply["text"] == "Это нестандартный случай, обсудим его на созвоне."

    audit_entries = system_harness.get_audit(booking_id=booking_id, event_type="escalation", limit=20)
    assert any(entry["action"] == "created" for entry in audit_entries)
    assert any(entry["action"] == "resolved" for entry in audit_entries)


def test_duplicate_webhook_is_ignored(system_harness) -> None:
    booking_id = "booking-duplicate"
    body = _booking_payload(booking_id)

    first = system_harness.post_planerka_webhook(body)
    second = system_harness.post_planerka_webhook(body)

    assert first == {"ok": True}
    assert second == {"ok": True, "duplicate": True}


def test_stop_trigger_escalates_even_with_high_confidence_model(system_harness) -> None:
    booking_id = "booking-stop-trigger"
    system_harness.post_planerka_webhook(_booking_payload(booking_id))

    system_harness.send_student_text(f"/start {booking_id}")
    system_harness.telegram.wait_for_sent_count(system_harness.student_token, 2)

    tutor_before = system_harness.telegram.sent_count(system_harness.owner_token)
    student_before = system_harness.telegram.sent_count(system_harness.student_token)

    system_harness.send_student_text("Позовите преподавателя, пожалуйста")

    tutor_notice = system_harness.telegram.wait_for_sent_message(
        system_harness.owner_token,
        after_index=tutor_before,
        chat_id=system_harness.tutor_chat_id,
        contains="Позовите преподавателя",
    )
    student_notice = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="передал",
    )

    assert "Позовите преподавателя" in tutor_notice["text"]
    assert "преподавателю" in student_notice["text"]


def test_student_without_booking_gets_contact_only_reply(system_harness) -> None:
    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    tutor_before = system_harness.telegram.sent_count(system_harness.owner_token)

    system_harness.send_student_text(
        "/start",
        username="no_booking_student",
        full_name="Student Without Booking",
    )
    greeting = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Привет! Чем могу помочь?",
    )
    assert greeting["text"] == "Привет! Чем могу помочь?"

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text(
        "Можно задать общий вопрос?",
        username="no_booking_student",
        full_name="Student Without Booking",
    )
    reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Стандартный ответ.",
    )

    assert reply["text"] == "Стандартный ответ."
    assert system_harness.telegram.sent_count(system_harness.owner_token) == tutor_before
    assert system_harness.list_tutor_escalations() == []


def test_student_with_multiple_bookings_must_choose_deeplink(system_harness) -> None:
    system_harness.post_planerka_webhook(
        _booking_payload_with_overrides(
            "booking-multi-a",
            title="Алгебра",
            start_time="2026-04-06T12:30:00+06:00",
            end_time="2026-04-06T13:00:00+06:00",
        )
    )
    system_harness.post_planerka_webhook(
        _booking_payload_with_overrides(
            "booking-multi-b",
            title="Геометрия",
            start_time="2026-04-07T15:00:00+06:00",
            end_time="2026-04-07T15:30:00+06:00",
        )
    )

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text("/start")
    notice = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="У вас несколько активных записей.",
    )

    assert "Алгебра" in notice["text"]
    assert "Геометрия" in notice["text"]

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text("/start booking-multi-b")
    system_harness.telegram.wait_for_sent_count(system_harness.student_token, student_before + 2)
    linked_messages = system_harness.telegram.sent_since(system_harness.student_token, student_before)

    assert any("Привет" in (item.get("text") or "") for item in linked_messages)
    assert any("Геометрия" in (item.get("text") or "") for item in linked_messages)

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text("Когда занятие и где ссылка?")
    reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Ссылка указана",
    )
    assert "Занятие в запланированное время" in reply["text"]


def test_multiple_parallel_escalations_are_independently_replyable(system_harness) -> None:
    booking_id = "booking-parallel-escalations"
    first_question = "У меня нестандартный вопрос номер один"
    second_question = "И еще вопрос номер два, который нужно обсудить вручную"

    system_harness.post_planerka_webhook(_booking_payload(booking_id))
    system_harness.send_student_text(f"/start {booking_id}")
    system_harness.telegram.wait_for_sent_count(system_harness.student_token, 2)

    tutor_before = system_harness.telegram.sent_count(system_harness.owner_token)
    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text(first_question)

    tutor_notice_one = system_harness.telegram.wait_for_sent_message(
        system_harness.owner_token,
        after_index=tutor_before,
        chat_id=system_harness.tutor_chat_id,
        contains=first_question,
    )
    student_notice_one = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="передал",
    )
    assert "ID брони" in tutor_notice_one["text"]
    assert "преподавателю" in student_notice_one["text"]

    tutor_before = system_harness.telegram.sent_count(system_harness.owner_token)
    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text(second_question)

    tutor_notice_two = system_harness.telegram.wait_for_sent_message(
        system_harness.owner_token,
        after_index=tutor_before,
        chat_id=system_harness.tutor_chat_id,
        contains=second_question,
    )
    student_notice_two = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="преподаватель ответит",
    )

    assert "ID брони" in tutor_notice_two["text"]
    assert "Ваш преподаватель ответит в ближайшее время." == student_notice_two["text"]

    pending = [
        item
        for item in system_harness.list_tutor_escalations()
        if item["booking_id"] == booking_id
    ]
    assert len(pending) == 2
    assert {item["question"] for item in pending} == {first_question, second_question}
    assert len({item["escalation_id"] for item in pending}) == 2

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_tutor_text(
        "Ответ на второй вопрос.",
        reply_to_message=tutor_notice_two,
    )
    second_reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Ответ на второй вопрос.",
    )
    assert second_reply["text"] == "Ответ на второй вопрос."

    pending_after_second_reply = [
        item
        for item in system_harness.list_tutor_escalations()
        if item["booking_id"] == booking_id
    ]
    assert len(pending_after_second_reply) == 1
    assert pending_after_second_reply[0]["question"] == first_question

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_tutor_text(
        "Ответ на первый вопрос.",
        reply_to_message=tutor_notice_one,
    )
    first_reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Ответ на первый вопрос.",
    )
    assert first_reply["text"] == "Ответ на первый вопрос."

    pending_after_all_replies = [
        item
        for item in system_harness.list_tutor_escalations()
        if item["booking_id"] == booking_id
    ]
    assert pending_after_all_replies == []


def test_tutor_reply_routes_contact_only_escalation_without_booking(system_harness) -> None:
    question = "У меня нестандартный общий вопрос без записи"

    tutor_before = system_harness.telegram.sent_count(system_harness.owner_token)
    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_student_text(question)

    tutor_notice = system_harness.telegram.wait_for_sent_message(
        system_harness.owner_token,
        after_index=tutor_before,
        chat_id=system_harness.tutor_chat_id,
        contains=question,
    )
    student_notice = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="передал",
    )

    assert "Общий вопрос без привязки к записи" in tutor_notice["text"]
    assert "ID брони" not in tutor_notice["text"]
    assert "преподавателю" in student_notice["text"]

    pending = [
        item
        for item in system_harness.list_tutor_escalations()
        if item["question"] == question
    ]
    assert len(pending) == 1
    assert pending[0]["booking_id"] is None

    student_before = system_harness.telegram.sent_count(system_harness.student_token)
    system_harness.send_tutor_text(
        "Ответ без брони дошел до студента.",
        reply_to_message=tutor_notice,
    )
    tutor_reply = system_harness.telegram.wait_for_sent_message(
        system_harness.student_token,
        after_index=student_before,
        chat_id=system_harness.student_chat_id,
        contains="Ответ без брони дошел до студента.",
    )
    assert tutor_reply["text"] == "Ответ без брони дошел до студента."

    pending_after_reply = [
        item
        for item in system_harness.list_tutor_escalations()
        if item["question"] == question
    ]
    assert pending_after_reply == []
