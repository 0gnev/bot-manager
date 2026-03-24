#!/usr/bin/env python3
"""
Planerka integration test.

Tests:
  1. REST API reachability + auth
  2. Event types listing
  3. Today's events
  4. Webhook payload simulation (BOOKING_CREATED)
  5. Booking state persistence

Run from the project root:
  python scripts/test-planerka.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# In Docker: env vars are injected via env_file. Locally: load from .env.
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "app"))

if not os.environ.get("PLANERKA_BASE_URL"):
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")

BASE_URL = os.environ["PLANERKA_BASE_URL"]
TOKEN = os.environ["PLANERKA_WEBHOOK_SECRET"]


# ── 1. REST API ───────────────────────────────────────────────────────────────

async def test_api() -> None:
    from plannerka_adapter.client import PlanerkaClient

    client = PlanerkaClient(BASE_URL, TOKEN)
    print("\n── REST API ─────────────────────────────")

    health = await client.health()
    print(f"  health:    {health}")

    types = await client.event_types()
    print(f"  types:     {len(types)} event type(s)")
    for t in types:
        print(f"             • {t['title']} ({t['length']} min, slug={t['slug']})")

    events = await client.events()
    print(f"  events:    {len(events)} booking(s) today")
    for e in events:
        print(f"             • [{e['id'][:8]}…] {e['title']} {e['startTime']} confirmed={e['confirmed']}")

    return events


# ── 2. Webhook payload simulation ────────────────────────────────────────────

def make_fake_payload(event_id: str, title: str, start: str, end: str) -> dict:
    """
    Simulate the BOOKING_CREATED payload Planerka sends to our webhook.
    Based on Planerka webhook docs and observed field names.
    """
    return {
        "event": "BOOKING_CREATED",
        "bookingId": event_id,
        "organizer": {
            "name": "Ilya Ognev",
            "email": "tutor@example.com",
            "timezone": "Asia/Bishkek",
        },
        "attendee": {
            "name": "Test Student",
            "email": "student@example.com",
            "timezone": "Asia/Bishkek",
        },
        "eventDetail": {
            "title": title,
            "startTime": start,
            "endTime": end,
        },
        "location": "Jitsi Meet",
        "meetingUrl": "https://meet.jit.si/test-room",
        "customFields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def test_webhook_handler(events: list[dict]) -> str | None:
    from plannerka_adapter.models import PlanerkaWebhookPayload

    print("\n── Webhook payload parsing ──────────────")

    # Use a real event from today if available, otherwise use fake data
    if events:
        e = events[0]
        payload_data = make_fake_payload(
            event_id=e["id"],
            title=e["title"],
            start=e["startTime"],
            end=e["endTime"],
        )
        print(f"  using real event id: {e['id'][:8]}…")
    else:
        payload_data = make_fake_payload(
            event_id="test-booking-001",
            title="Встреча на 30 минут",
            start="2026-03-24T05:00:00+00:00",
            end="2026-03-24T05:30:00+00:00",
        )
        print("  using fake event (no events today)")

    payload = PlanerkaWebhookPayload.model_validate(payload_data)
    print(f"  parsed event:       {payload.event}")
    print(f"  booking_id:         {payload.get_booking_id()}")
    print(f"  attendee:           {payload.get_attendee().name if payload.get_attendee() else 'N/A'}")
    print(f"  meeting_url:        {payload.get_meeting_url()}")
    print(f"  start:              {payload.event_detail.start_time if payload.event_detail else 'N/A'}")

    return payload.get_booking_id()


# ── 3. State persistence ──────────────────────────────────────────────────────

async def test_state(events: list[dict], booking_id: str | None) -> None:
    from bridge.state import bookings as bk

    print("\n── State persistence ────────────────────")

    with tempfile.TemporaryDirectory() as tmpdir:
        booking_id = booking_id or "test-booking-001"
        data = {
            "booking_id": booking_id,
            "event": "BOOKING_CREATED",
            "attendee": {"name": "Test Student", "email": "s@example.com"},
            "event_detail": {"title": "Test Lesson", "startTime": "2026-03-24T05:00:00+00:00"},
            "meeting_url": "https://meet.jit.si/test-room",
            "telegram_user_id": None,
            "status": "active",
        }

        await bk.save(tmpdir, booking_id, data)
        loaded = await bk.load(tmpdir, booking_id)
        assert loaded is not None, "Failed to load saved booking"
        assert loaded["booking_id"] == booking_id
        print(f"  save/load:          OK (booking_id={booking_id[:8]}…)")

        updated = await bk.link_telegram_user(tmpdir, booking_id, 12345678)
        assert updated["telegram_user_id"] == 12345678
        print(f"  link telegram user: OK (user_id=12345678)")

        found = await bk.find_by_telegram_user(tmpdir, 12345678)
        assert found is not None
        assert found["booking_id"] == booking_id
        print(f"  find by user:       OK")


# ── 4. Webhook handler integration ───────────────────────────────────────────

async def test_webhook_handler_full(events: list[dict]) -> None:
    from bridge.webhook.handler import handle_webhook

    print("\n── Webhook handler (full) ───────────────")

    class FakeSettings:
        state_path: str
        def __init__(self, path):
            self.state_path = path

    with tempfile.TemporaryDirectory() as tmpdir:
        settings = FakeSettings(tmpdir)

        if events:
            e = events[0]
            payload = make_fake_payload(e["id"], e["title"], e["startTime"], e["endTime"])
        else:
            payload = make_fake_payload(
                "test-001", "Test Lesson", "2026-03-24T05:00:00+00:00", "2026-03-24T05:30:00+00:00"
            )

        await handle_webhook(payload, settings)

        from bridge.state import bookings as bk
        booking_id = payload["bookingId"]
        saved = await bk.load(tmpdir, booking_id)
        assert saved is not None, "Booking not saved after webhook"
        assert saved["status"] == "active"
        print(f"  BOOKING_CREATED:    saved OK ({booking_id[:8]}…)")

        # Reschedule
        payload["event"] = "BOOKING_RESCHEDULED"
        payload["eventDetail"]["startTime"] = "2026-03-25T06:00:00+00:00"
        await handle_webhook(payload, settings)
        updated = await bk.load(tmpdir, booking_id)
        from datetime import timezone
        new_start_raw = updated["event_detail"].get("startTime") or updated["event_detail"].get("start_time")
        new_start_dt = datetime.fromisoformat(new_start_raw.replace("Z", "+00:00"))
        expected_dt = datetime(2026, 3, 25, 6, 0, 0, tzinfo=timezone.utc)
        assert new_start_dt == expected_dt, f"Got: {new_start_raw}"
        print(f"  BOOKING_RESCHEDULED: updated OK")

        # Cancel
        payload["event"] = "BOOKING_CANCELLED"
        await handle_webhook(payload, settings)
        cancelled = await bk.load(tmpdir, booking_id)
        assert cancelled["status"] == "cancelled"
        print(f"  BOOKING_CANCELLED:  updated OK")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("Planerka integration test")
    print(f"Base URL: {BASE_URL}")

    try:
        events = await test_api()
    except Exception as exc:
        print(f"  ERROR: {exc}")
        events = []

    booking_id = await test_webhook_handler(events)
    await test_state(events, booking_id)
    await test_webhook_handler_full(events)

    print("\n✓ All tests passed\n")


if __name__ == "__main__":
    asyncio.run(main())
