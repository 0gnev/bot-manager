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

        assert audit_events == [
            (
                "delivery",
                "student_message_sent",
                {
                    "booking_id": "booking-1",
                    "actor": "system",
                    "detail": {"source": "test", "chat_id": 42},
                },
            )
        ]

    run_async(_run())
