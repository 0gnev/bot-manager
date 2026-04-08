"""
State workflow tests — backed by PostgreSQL.

These tests exercise the state module functions against a real PostgreSQL
instance configured in conftest.py via the ``pg_pool`` fixture.
"""

from __future__ import annotations

from conftest import run_async

from bridge.state import (
    approvals,
    bookings,
    contacts,
    conversations,
    escalations,
    load_controls,
    save_controls,
    save_tutor_time_zone,
)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _create_booking(booking_id: str = "booking-1", **overrides) -> dict:
    data = {
        "booking_id": booking_id,
        "event": "BOOKING_CREATED",
        "title": "Test Lesson",
        "status": "active",
        "attendee": {
            "name": "Test Student",
            "email": "student@example.com",
            "telegram": "@teststudent",
            "timeZone": "Asia/Bishkek",
        },
        "organizer": {"name": "Tutor", "email": "tutor@example.com"},
        **overrides,
    }
    await bookings.save("", booking_id, data)
    return data


# ── approval tests ───────────────────────────────────────────────────────────


def test_approval_state_persists_review_metadata(db_clean) -> None:
    async def _run():
        await _create_booking("booking-1")

        created = await approvals.create_approval(
            "",
            "booking-1",
            student_chat_id=123,
            draft_content="Черновик",
            action="answer",
            confidence=0.6,
        )
        resolved = await approvals.resolve_approval(
            "",
            created["approval_id"],
            "approved",
            reviewer="tutor",
            review_channel="api",
        )

        assert created["status"] == "pending"
        assert resolved["status"] == "approved"
        assert resolved["reviewer"] == "tutor"
        assert resolved["review_channel"] == "api"

    run_async(_run())


def test_contact_only_approval_state_persists_contact_scope(db_clean) -> None:
    async def _run():
        contact = await contacts.ensure_telegram_contact(
            "",
            555,
            telegram_username="general_student",
            name="General Student",
        )

        created = await approvals.create_approval(
            "",
            None,
            contact_id=contact["id"],
            student_chat_id=555,
            draft_content="Черновик без записи",
            action="answer",
            confidence=0.55,
        )
        pending = await approvals.list_pending("", contact_id=contact["id"])

        assert created["booking_id"] is None
        assert created["contact_id"] == contact["id"]
        assert len(pending) == 1
        assert pending[0]["approval_id"] == created["approval_id"]

    run_async(_run())


def test_pending_approval_draft_can_be_revised_in_place(db_clean) -> None:
    async def _run():
        await _create_booking("booking-approval-revise")

        created = await approvals.create_approval(
            "",
            "booking-approval-revise",
            student_chat_id=123,
            draft_content="Старый черновик",
            action="answer",
            confidence=0.6,
        )
        updated = await approvals.update_pending_draft(
            "",
            created["approval_id"],
            "Новый черновик",
            tutor_message_id=456,
        )

        assert updated is not None
        assert updated["draft_content"] == "Новый черновик"
        assert updated["tutor_message_id"] == 456
        assert updated["status"] == "pending"

    run_async(_run())


def test_conversation_messages_store_delivery_transport_and_attachments(db_clean) -> None:
    async def _run():
        contact = await contacts.ensure_telegram_contact(
            "",
            777,
            telegram_username="media_student",
            name="Media Student",
        )

        await conversations.append(
            "",
            "user",
            "[image] Прислал фото",
            contact_id=contact["id"],
            direction="inbound",
            source="telegram_photo",
            delivery_status="received",
            transport_chat_id=777,
            transport_message_id=321,
            attachments=[
                {
                    "file_id": "photo-file",
                    "file_type": "photo",
                    "mime_type": "image/jpeg",
                    "local_path": "/tmp/photo.jpg",
                    "caption": "Прислал фото",
                }
            ],
        )
        await conversations.append(
            "",
            "assistant",
            "Ответ бота",
            contact_id=contact["id"],
            direction="outbound",
            source="ai_answer",
            delivery_status="sent",
            transport_chat_id=777,
            transport_message_id=322,
            model_output={"action": "answer", "confidence": 0.93},
        )

        chat = await conversations.load_chat_by_contact("", contact["id"])

        assert len(chat.messages) == 2
        assert chat.messages[0]["attachments"][0]["file_id"] == "photo-file"
        assert chat.messages[0]["transport_message_id"] == 321
        assert chat.messages[1]["delivery_status"] == "sent"
        assert chat.messages[1]["source"] == "ai_answer"
        assert chat.messages[1]["model_output"]["confidence"] == 0.93

    run_async(_run())


# ── escalation tests ─────────────────────────────────────────────────────────


def test_escalation_state_persists_reason_and_resolver(db_clean) -> None:
    async def _run():
        await _create_booking("booking-2")

        created = await escalations.create(
            "",
            "booking-2",
            question="Нужен человек",
            tutor_message_id=555,
            reason="human_review_required",
            summary="Контекст: Встреча. Причина: нужна проверка преподавателя.",
            relevant_history=[
                {"role": "user", "content": "Здравствуйте"},
                {"role": "assistant", "content": "Добрый день"},
            ],
            draft_reply="Могу уточнить детали у преподавателя.",
        )
        resolved = await escalations.resolve(
            "",
            "booking-2",
            "Отвечаю вручную",
            resolved_by="tutor",
        )

        assert created["reason"] == "human_review_required"
        assert created["status"] == "pending"
        assert created["summary"] == "Контекст: Встреча. Причина: нужна проверка преподавателя."
        assert created["relevant_history"][0]["content"] == "Здравствуйте"
        assert created["draft_reply"] == "Могу уточнить детали у преподавателя."
        assert resolved["status"] == "resolved"
        assert resolved["resolved_by"] == "tutor"
        assert resolved["summary"] == created["summary"]

    run_async(_run())


def test_multiple_escalations_per_booking(db_clean) -> None:
    async def _run():
        await _create_booking("booking-multi")

        first = await escalations.create("", "booking-multi", question="Q1", reason="r1")
        second = await escalations.create("", "booking-multi", question="Q2", reason="r2")

        # load returns the latest by created_at
        latest = await escalations.load("", "booking-multi")
        assert latest["question"] == "Q2"

        pending = await escalations.list_pending("", booking_id="booking-multi")
        assert [item["escalation_id"] for item in pending] == [
            first["escalation_id"],
            second["escalation_id"],
        ]

        by_id = await escalations.load_by_id("", first["escalation_id"])
        assert by_id is not None
        assert by_id["question"] == "Q1"

        # resolve resolves the latest pending
        resolved = await escalations.resolve("", "booking-multi", "Answer to Q2", resolved_by="tutor")
        assert resolved["question"] == "Q2"
        assert resolved["status"] == "resolved"

        resolved_first = await escalations.resolve_by_id(
            "",
            first["escalation_id"],
            "Answer to Q1",
            resolved_by="tutor",
        )
        assert resolved_first is not None
        assert resolved_first["question"] == "Q1"
        assert resolved_first["status"] == "resolved"

    run_async(_run())


# ── runtime controls tests ───────────────────────────────────────────────────


def test_runtime_controls_persist_global_automation_state(db_clean) -> None:
    async def _run():
        initial = await load_controls("")
        updated = await save_controls(
            "",
            global_automation_enabled=False,
            updated_by="tutor",
            reason="maintenance",
        )
        reloaded = await load_controls("")

        assert initial["global_automation_enabled"] is True
        assert updated["global_automation_enabled"] is False
        assert reloaded["updated_by"] == "tutor"
        assert reloaded["reason"] == "maintenance"

    run_async(_run())


def test_runtime_controls_persist_tutor_time_zone(db_clean) -> None:
    async def _run():
        updated = await save_tutor_time_zone(
            "",
            tutor_time_zone="Europe/Moscow",
            updated_by="tutor",
            reason="timezone setup",
        )
        reloaded = await load_controls("")

        assert updated["tutor_time_zone"] == "Europe/Moscow"
        assert reloaded["tutor_time_zone"] == "Europe/Moscow"
        assert reloaded["updated_by"] == "tutor"
        assert reloaded["reason"] == "timezone setup"

    run_async(_run())


# ── booking tests ────────────────────────────────────────────────────────────


def test_booking_save_and_load(db_clean) -> None:
    async def _run():
        await _create_booking("booking-save")
        loaded = await bookings.load("", "booking-save")

        assert loaded is not None
        assert loaded["booking_id"] == "booking-save"
        assert loaded["status"] == "active"
        assert loaded["title"] == "Test Lesson"

    run_async(_run())


def test_booking_link_telegram_user(db_clean) -> None:
    async def _run():
        await _create_booking("booking-link")
        updated = await bookings.link_telegram_user("", "booking-link", 12345)

        assert updated is not None
        assert updated["telegram_user_id"] == 12345

        found = await bookings.find_by_telegram_user("", 12345)
        assert found is not None
        assert found["booking_id"] == "booking-link"

    run_async(_run())


def test_booking_requires_active_context_when_multiple_active_bookings(db_clean) -> None:
    async def _run():
        await _create_booking("booking-a", telegram_user_id=12345)
        await _create_booking("booking-b", telegram_user_id=12345)

        found = await bookings.find_by_telegram_user("", 12345)
        assert found is None

        all_found = await bookings.find_all_by_telegram_user("", 12345)
        assert {item["booking_id"] for item in all_found} == {"booking-a", "booking-b"}

        await bookings.link_telegram_user("", "booking-b", 12345)
        active = await bookings.find_by_telegram_user("", 12345)
        assert active is not None
        assert active["booking_id"] == "booking-b"

    run_async(_run())


def test_booking_find_by_username(db_clean) -> None:
    async def _run():
        await _create_booking("booking-username")
        found = await bookings.find_by_telegram_username("", "teststudent")

        assert found is not None
        assert found["booking_id"] == "booking-username"

    run_async(_run())


def test_booking_save_inherits_linked_contact_telegram_user(db_clean) -> None:
    async def _run():
        await _create_booking("booking-linked-a")
        await bookings.link_telegram_user("", "booking-linked-a", 12345)
        await _create_booking("booking-linked-b")

        found = await bookings.load("", "booking-linked-b")
        assert found is not None
        assert found["telegram_user_id"] == 12345

    run_async(_run())


def test_attach_telegram_identity_merges_existing_user_contact(db_clean) -> None:
    async def _run():
        standalone = await contacts.ensure_telegram_contact(
            "",
            888,
            telegram_username="standalone_student",
            name="Standalone Student",
        )
        await _create_booking("booking-merge-contact")
        booking = await bookings.load("", "booking-merge-contact")
        assert booking is not None
        source_contact_id = booking["contact_id"]
        assert source_contact_id is not None
        assert source_contact_id != standalone["id"]

        merged = await contacts.attach_telegram_identity(
            "",
            source_contact_id,
            telegram_user_id=888,
            telegram_username="teststudent",
            name="Merged Student",
        )

        assert merged is not None
        assert merged["id"] == standalone["id"]
        merged_booking = await bookings.load("", "booking-merge-contact")
        assert merged_booking is not None
        assert merged_booking["contact_id"] == standalone["id"]

    run_async(_run())


# ── conversation tests ───────────────────────────────────────────────────────


def test_conversation_append_and_load(db_clean) -> None:
    async def _run():
        await _create_booking("booking-conv")
        await conversations.append("", "user", "Hello!", booking_id="booking-conv")
        await conversations.append("", "assistant", "Hi there!", booking_id="booking-conv")

        msgs = await conversations.load("", booking_id="booking-conv")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello!"
        assert msgs[1]["role"] == "assistant"

    run_async(_run())


def test_contact_scoped_conversation_without_booking(db_clean) -> None:
    async def _run():
        contact = await contacts.ensure_telegram_contact(
            "",
            777,
            telegram_username="joji5213",
            name="Ivan Petrov",
        )

        await conversations.append("", "user", "Привет", contact_id=contact["id"])
        await conversations.append("", "assistant", "Здравствуйте", contact_id=contact["id"])

        chat = await conversations.load_chat_by_contact("", contact["id"])
        assert chat.contact_id == contact["id"]
        assert chat.booking_id is None
        assert [item["content"] for item in chat.messages] == ["Привет", "Здравствуйте"]

    run_async(_run())


def test_contact_only_escalation(db_clean) -> None:
    async def _run():
        contact = await contacts.ensure_telegram_contact(
            "",
            778,
            telegram_username="without_booking",
            name="No Booking",
        )

        created = await escalations.create(
            "",
            None,
            contact_id=contact["id"],
            question="Общий вопрос",
            reason="human_review_required",
        )
        pending = await escalations.list_pending("", contact_id=contact["id"])

        assert created["booking_id"] is None
        assert created["contact_id"] == contact["id"]
        assert len(pending) == 1
        assert pending[0]["question"] == "Общий вопрос"

    run_async(_run())


def test_booking_context_resolver_prefers_future_booking(db_clean) -> None:
    async def _run():
        await _create_booking(
            "booking-past",
            telegram_user_id=12345,
            start_time="2026-04-01T10:00:00+06:00",
        )
        future = await _create_booking(
            "booking-future",
            telegram_user_id=12345,
            start_time="2026-04-07T10:00:00+06:00",
        )

        contact = await contacts.load_by_telegram_user("", 12345)
        assert contact is not None

        resolved = await bookings.resolve_context_for_contact("", contact["id"])
        assert resolved is not None
        assert resolved["booking_id"] == future["booking_id"]

    run_async(_run())


def test_conversation_update_metadata(db_clean) -> None:
    async def _run():
        await _create_booking("booking-meta")

        chat = await conversations.update_metadata(
            "", "booking-meta",
            escalation_state="pending",
            current_stage="escalated",
        )
        assert chat.escalation_state == "pending"
        assert chat.current_stage == "escalated"

    run_async(_run())
