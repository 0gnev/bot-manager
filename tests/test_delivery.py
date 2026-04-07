from __future__ import annotations

from types import SimpleNamespace

from conftest import run_async

from bridge.delivery.service import send_student_message
from bridge.state import bookings, conversations


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent_messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=501, chat=SimpleNamespace(id=chat_id))


def test_send_student_message_records_history_and_audit(db_clean) -> None:
    settings = SimpleNamespace(state_path="")
    bot = DummyBot()
    audit_events: list[tuple[str, str, dict]] = []

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        audit_events.append((event_type, action, kwargs))

    from bridge import delivery as delivery_pkg
    from bridge.delivery import service

    service.audit_log = fake_audit_log
    delivery_pkg.send_student_message  # keep import live for module initialization

    async def _run():
        # Create booking first (conversations.append requires it)
        await bookings.save("", "booking-1", {
            "booking_id": "booking-1",
            "status": "active",
            "title": "Test",
            "attendee": {"name": "Student", "email": "s@test.com"},
        })

        result = await send_student_message(
            bot=bot,
            chat_id=42,
            text="Привет!",
            booking_id="booking-1",
            settings=settings,
            source="test",
        )

        assert result is True
        assert bot.sent_messages == [(42, "Привет!", {})]

        history = await conversations.load("", "booking-1")
        assert len(history) == 1
        assert history[0]["role"] == "assistant"
        assert history[0]["content"] == "Привет!"
        assert history[0]["direction"] == "outbound"
        assert history[0]["delivery_status"] == "sent"
        assert history[0]["source"] == "test"
        assert history[0]["transport_chat_id"] == 42
        assert history[0]["transport_message_id"] == 501

        assert audit_events == [
            (
                "delivery",
                "student_message_sent",
                {
                    "booking_id": "booking-1",
                    "actor": "system",
                    "detail": {"source": "test", "chat_id": 42, "attempts": 1},
                },
            )
        ]

    run_async(_run())


def test_send_student_message_records_failed_delivery_metadata(db_clean) -> None:
    settings = SimpleNamespace(state_path="")
    audit_events: list[tuple[str, str, dict]] = []

    class FailingBot:
        async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
            raise RuntimeError("telegram down")

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        audit_events.append((event_type, action, kwargs))

    from bridge import delivery as delivery_pkg
    from bridge.delivery import service

    service.audit_log = fake_audit_log
    delivery_pkg.send_student_message

    async def _run():
        await bookings.save("", "booking-1", {
            "booking_id": "booking-1",
            "status": "active",
            "title": "Test",
            "attendee": {"name": "Student", "email": "s@test.com"},
        })

        result = await send_student_message(
            bot=FailingBot(),
            chat_id=42,
            text="Привет!",
            booking_id="booking-1",
            settings=settings,
            source="test",
        )

        assert result is False
        history = await conversations.load("", "booking-1")
        assert len(history) == 1
        assert history[0]["delivery_status"] == "failed"
        assert history[0]["direction"] == "outbound"
        assert history[0]["transport_chat_id"] == 42
        assert any(
            event_type == "delivery"
            and action == "student_message_sent"
            and kwargs.get("outcome") == "failure"
            for event_type, action, kwargs in audit_events
        )

    run_async(_run())


def test_send_student_message_retries_transient_failure_then_succeeds(db_clean) -> None:
    settings = SimpleNamespace(
        state_path="",
        telegram_delivery_attempts=3,
        telegram_delivery_backoff_seconds=0,
    )
    audit_events: list[tuple[str, str, dict]] = []

    class FlakyBot:
        def __init__(self) -> None:
            self.calls = 0

        async def send_message(self, chat_id: int, text: str, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("temporary telegram error")
            return SimpleNamespace(message_id=777, chat=SimpleNamespace(id=chat_id))

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        audit_events.append((event_type, action, kwargs))

    from bridge import delivery as delivery_pkg
    from bridge.delivery import service

    service.audit_log = fake_audit_log
    delivery_pkg.send_student_message

    async def _run():
        await bookings.save("", "booking-1", {
            "booking_id": "booking-1",
            "status": "active",
            "title": "Test",
            "attendee": {"name": "Student", "email": "s@test.com"},
        })

        bot = FlakyBot()
        result = await send_student_message(
            bot=bot,
            chat_id=42,
            text="Привет!",
            booking_id="booking-1",
            settings=settings,
            source="retry-test",
        )

        assert result is True
        assert bot.calls == 3
        history = await conversations.load("", "booking-1")
        assert len(history) == 1
        assert history[0]["delivery_status"] == "sent"
        assert history[0]["transport_message_id"] == 777
        assert audit_events[0][2]["detail"]["attempts"] == 3

    run_async(_run())
