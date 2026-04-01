from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from bridge.api import tutor as tutor_api


def test_tutor_reply_keeps_escalation_pending_when_delivery_fails(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=None)
    body = tutor_api.ReplyRequest(booking_id="booking-1", text="Ответ преподавателя")
    calls = {"resolved": False, "metadata_updated": False}

    async def fake_load_escalation(*args, **kwargs) -> dict:
        return {"booking_id": "booking-1", "status": "pending"}

    async def fake_load_booking(*args, **kwargs) -> dict:
        return {"booking_id": "booking-1", "telegram_user_id": 123}

    async def fake_send_student_message(**kwargs) -> bool:
        return False

    async def fake_resolve(*args, **kwargs) -> None:
        calls["resolved"] = True

    async def fake_update_metadata(*args, **kwargs) -> None:
        calls["metadata_updated"] = True

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(tutor_api.escalations, "load", fake_load_escalation)
    monkeypatch.setattr(tutor_api.bookings, "load", fake_load_booking)
    monkeypatch.setattr(tutor_api.escalations, "resolve", fake_resolve)
    monkeypatch.setattr(tutor_api.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(tutor_api, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(tutor_api, "audit_log", fake_audit_log)
    monkeypatch.setattr(tutor_api.registry, "get_student", lambda: object())
    monkeypatch.setattr(tutor_api.registry, "get_owner", lambda: None)

    try:
        asyncio.run(tutor_api.tutor_reply(body, settings))
    except HTTPException as exc:
        assert exc.status_code == 502
        assert exc.detail == "Failed to deliver tutor reply to student"
    else:
        assert False, "Expected tutor_reply to raise when student delivery fails"

    assert calls["resolved"] is False
    assert calls["metadata_updated"] is False
