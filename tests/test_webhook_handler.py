from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from conftest import run_async

from bridge.state import bookings, conversations
from bridge.webhook.handler import handle_webhook


def test_booking_created_webhook_persists_iso_datetime_fields(db_clean, monkeypatch) -> None:
    settings = SimpleNamespace(state_path="")

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr("bridge.webhook.handler.audit_log", fake_audit_log)

    body = {
        "event": "BOOKING_CREATED",
        "uid": "booking-webhook",
        "title": "Webhook Lesson",
        "description": "",
        "startTime": "2026-04-06T12:30:00+06:00",
        "endTime": "2026-04-06T13:00:00+06:00",
        "organizer": {
            "name": "Tutor",
            "email": "tutor@example.com",
            "timeZone": "Europe/Moscow",
        },
        "attendees": [
            {
                "name": "Student",
                "email": "student@example.com",
                "telegram": "@student",
                "timeZone": "Asia/Bishkek",
            }
        ],
        "location": {
            "name": "Jitsi",
            "url": "https://meet.jit.si/example",
        },
    }

    async def _run() -> None:
        await handle_webhook(body, settings)

        booking = await bookings.load("", "booking-webhook")
        assert booking is not None
        assert datetime.fromisoformat(booking["start_time"]) == datetime.fromisoformat(body["startTime"])
        assert datetime.fromisoformat(booking["end_time"]) == datetime.fromisoformat(body["endTime"])

        chat = await conversations.load_chat("", "booking-webhook")
        assert chat.current_stage == "booking_created"
        assert chat.automation_enabled is True

    run_async(_run())
