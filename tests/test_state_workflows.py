"""
State workflow tests — backed by PostgreSQL.

These tests exercise the state module functions against a real PostgreSQL
instance configured in conftest.py via the ``pg_pool`` fixture.
"""

from __future__ import annotations

from conftest import run_async

from bridge.state import approvals, bookings, conversations, escalations, load_controls, save_controls


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


def test_approval_state_persists_review_metadata(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-1")

        created = await approvals.create_approval(
            "",
            booking_id="booking-1",
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


# ── escalation tests ─────────────────────────────────────────────────────────


def test_escalation_state_persists_reason_and_resolver(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-2")

        created = await escalations.create(
            "",
            "booking-2",
            question="Нужен человек",
            tutor_message_id=555,
            reason="human_review_required",
        )
        resolved = await escalations.resolve(
            "",
            "booking-2",
            "Отвечаю вручную",
            resolved_by="tutor",
        )

        assert created["reason"] == "human_review_required"
        assert created["status"] == "pending"
        assert resolved["status"] == "resolved"
        assert resolved["resolved_by"] == "tutor"

    run_async(_run())


def test_multiple_escalations_per_booking(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-multi")

        await escalations.create("", "booking-multi", question="Q1", reason="r1")
        await escalations.create("", "booking-multi", question="Q2", reason="r2")

        # load returns the latest by created_at
        latest = await escalations.load("", "booking-multi")
        assert latest["question"] == "Q2"

        # resolve resolves the latest pending
        resolved = await escalations.resolve("", "booking-multi", "Answer to Q2", resolved_by="tutor")
        assert resolved["question"] == "Q2"
        assert resolved["status"] == "resolved"

    run_async(_run())


# ── runtime controls tests ───────────────────────────────────────────────────


def test_runtime_controls_persist_global_automation_state(pg_pool) -> None:
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


# ── booking tests ────────────────────────────────────────────────────────────


def test_booking_save_and_load(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-save")
        loaded = await bookings.load("", "booking-save")

        assert loaded is not None
        assert loaded["booking_id"] == "booking-save"
        assert loaded["status"] == "active"
        assert loaded["title"] == "Test Lesson"

    run_async(_run())


def test_booking_link_telegram_user(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-link")
        updated = await bookings.link_telegram_user("", "booking-link", 12345)

        assert updated is not None
        assert updated["telegram_user_id"] == 12345

        found = await bookings.find_by_telegram_user("", 12345)
        assert found is not None
        assert found["booking_id"] == "booking-link"

    run_async(_run())


def test_booking_find_by_username(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-username")
        found = await bookings.find_by_telegram_username("", "teststudent")

        assert found is not None
        assert found["booking_id"] == "booking-username"

    run_async(_run())


# ── conversation tests ───────────────────────────────────────────────────────


def test_conversation_append_and_load(pg_pool) -> None:
    async def _run():
        await _create_booking("booking-conv")
        await conversations.append("", "booking-conv", "user", "Hello!")
        await conversations.append("", "booking-conv", "assistant", "Hi there!")

        msgs = await conversations.load("", "booking-conv")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello!"
        assert msgs[1]["role"] == "assistant"

    run_async(_run())


def test_conversation_update_metadata(pg_pool) -> None:
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
