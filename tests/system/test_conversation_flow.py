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
