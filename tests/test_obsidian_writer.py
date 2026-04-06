from __future__ import annotations

import asyncio

from obsidian_adapter import writer


def test_export_contact_scoped_conversation_uses_contact_doc_id(tmp_path) -> None:
    async def _run() -> None:
        await writer.export_conversation(
            str(tmp_path),
            [
                {
                    "role": "user",
                    "content": "Привет",
                    "ts": "2026-04-06T06:00:00+00:00",
                }
            ],
            contact_id=11,
            contact={"id": 11, "name": "Ivan Petrov", "telegram_username": "joji5213"},
        )

    asyncio.run(_run())

    path = tmp_path / "conversations" / "contact-11.md"
    assert path.exists()

    content = path.read_text(encoding="utf-8")
    assert "contact_id: 11" in content
    assert "booking_id: None" not in content
    assert "# Диалог — Ivan Petrov" in content
    assert "Привет" in content


def test_export_contact_only_escalation_uses_contact_doc_id(tmp_path) -> None:
    async def _run() -> None:
        await writer.export_escalation(
            str(tmp_path),
            {
                "booking_id": None,
                "contact_id": 11,
                "escalation_id": 5,
                "status": "pending",
                "question": "Общий вопрос",
                "created_at": "2026-04-06T06:00:00+00:00",
                "resolved_at": None,
            },
            contact={"id": 11, "name": "Ivan Petrov", "telegram_username": "joji5213"},
        )

    asyncio.run(_run())

    path = tmp_path / "escalations" / "contact-11-5.md"
    assert path.exists()

    content = path.read_text(encoding="utf-8")
    assert "contact_id: 11" in content
    assert "booking_id: None" not in content
    assert "resolved_at: None" not in content
    assert "# Эскалация — Ivan Petrov / 5" in content
    assert "Общий вопрос" in content


def test_export_booking_includes_contact_id(tmp_path) -> None:
    async def _run() -> None:
        await writer.export_booking(
            str(tmp_path),
            {
                "booking_id": "booking-1",
                "contact_id": 11,
                "status": "active",
                "title": "Lesson",
                "attendee": {"name": "Ivan Petrov"},
                "organizer": {"name": "Tutor"},
            },
        )

    asyncio.run(_run())

    path = tmp_path / "bookings" / "booking-1.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "contact_id: 11" in content
    assert "telegram_user_id: None" not in content
