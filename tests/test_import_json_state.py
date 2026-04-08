from __future__ import annotations

import json
from pathlib import Path

from conftest import run_async

from bridge.db import get_pool
from scripts.import_json_state import run_import


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_import_json_state_is_rerunnable_without_duplicates(db_clean, database_url, tmp_path) -> None:
    state_path = tmp_path / "state"
    booking_id = "booking-import"

    _write_json(
        state_path / "bookings" / f"{booking_id}.json",
        {
            "booking_id": booking_id,
            "event": "BOOKING_CREATED",
            "title": "Imported booking",
            "status": "active",
            "telegram_user_id": None,
            "attendee": {
                "name": "Imported Student",
                "email": "student@example.com",
                "telegram": "@importstudent",
                "timeZone": "Asia/Bishkek",
            },
            "organizer": {"name": "Tutor", "email": "tutor@example.com"},
        },
    )
    _write_json(
        state_path / "conversations" / f"{booking_id}.json",
        {
            "booking_id": booking_id,
            "messages": [
                {
                    "role": "user",
                    "content": "Первый вопрос",
                    "ts": "2026-04-06T01:00:00+00:00",
                    "direction": "inbound",
                    "source": "telegram_message",
                    "delivery_status": "received",
                    "transport_chat_id": 321,
                    "transport_message_id": 654,
                },
                {"role": "assistant", "content": "Первый ответ", "ts": "2026-04-06T01:01:00+00:00"},
            ],
            "mode": "auto",
            "automation_enabled": True,
        },
    )
    _write_json(
        state_path / "escalations" / f"{booking_id}.json",
        {
            "booking_id": booking_id,
            "status": "pending",
            "reason": "human_review_required",
            "question": "Нужен человек",
            "tutor_message_id": 123,
            "created_at": "2026-04-06T01:02:00+00:00",
            "resolved_at": None,
        },
    )

    async def _run() -> None:
        await run_import(str(state_path), database_url)
        await run_import(str(state_path), database_url)

        pool = get_pool()
        contacts_count = await pool.fetchval("SELECT COUNT(*) FROM contacts")
        messages_count = await pool.fetchval(
            "SELECT COUNT(*) FROM messages WHERE booking_id = $1",
            booking_id,
        )
        messages_with_contact_count = await pool.fetchval(
            "SELECT COUNT(*) FROM messages WHERE booking_id = $1 AND contact_id IS NOT NULL",
            booking_id,
        )
        escalations_count = await pool.fetchval(
            "SELECT COUNT(*) FROM escalations WHERE booking_id = $1",
            booking_id,
        )
        contact_channels_count = await pool.fetchval(
            "SELECT COUNT(*) FROM contact_channels",
        )
        deliveries_count = await pool.fetchval(
            "SELECT COUNT(*) FROM deliveries WHERE booking_id = $1",
            booking_id,
        )

        assert contacts_count == 1
        assert messages_count == 2
        assert messages_with_contact_count == 2
        assert escalations_count == 1
        assert contact_channels_count >= 2
        assert deliveries_count == 1

    run_async(_run())
