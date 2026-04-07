from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from bridge.api import tutor as tutor_api


def test_tutor_reply_keeps_escalation_pending_when_delivery_fails(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=None)
    body = tutor_api.ReplyRequest(booking_id="booking-1", text="Ответ преподавателя")
    calls = {"resolved": False, "metadata_updated": False}

    async def fake_list_pending(*args, **kwargs) -> list[dict]:
        return [{"booking_id": "booking-1", "status": "pending", "escalation_id": 11}]

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

    monkeypatch.setattr(tutor_api.escalations, "list_pending", fake_list_pending)
    monkeypatch.setattr(tutor_api.bookings, "load", fake_load_booking)
    monkeypatch.setattr(tutor_api.escalations, "resolve_by_id", fake_resolve)
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


def test_tutor_reply_requires_escalation_id_when_multiple_pending(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=None)
    body = tutor_api.ReplyRequest(booking_id="booking-1", text="Ответ преподавателя")

    async def fake_list_pending(*args, **kwargs) -> list[dict]:
        return [
            {"booking_id": "booking-1", "status": "pending", "escalation_id": 11},
            {"booking_id": "booking-1", "status": "pending", "escalation_id": 12},
        ]

    monkeypatch.setattr(tutor_api.escalations, "list_pending", fake_list_pending)

    try:
        asyncio.run(tutor_api.tutor_reply(body, settings))
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "Multiple pending escalations for this booking; specify escalation_id"
    else:
        assert False, "Expected tutor_reply to reject ambiguous booking-level replies"


def test_tutor_reply_resolves_specific_escalation_id(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=None)
    body = tutor_api.ReplyRequest(escalation_id=22, text="Ответ преподавателя")
    calls = {"resolved_id": None}

    async def fake_load_by_id(*args, **kwargs) -> dict:
        return {"booking_id": "booking-1", "status": "pending", "escalation_id": 22}

    async def fake_load_booking(*args, **kwargs) -> dict:
        return {"booking_id": "booking-1", "telegram_user_id": 123}

    async def fake_send_student_message(**kwargs) -> bool:
        return True

    async def fake_resolve_by_id(state_path: str, escalation_id: int, tutor_reply: str, resolved_by: str | None = None):
        calls["resolved_id"] = escalation_id
        return {"booking_id": "booking-1", "status": "resolved", "escalation_id": escalation_id}

    async def fake_update_metadata(*args, **kwargs) -> None:
        return None

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(tutor_api.escalations, "load_by_id", fake_load_by_id)
    monkeypatch.setattr(tutor_api.bookings, "load", fake_load_booking)
    monkeypatch.setattr(tutor_api.escalations, "resolve_by_id", fake_resolve_by_id)
    monkeypatch.setattr(tutor_api.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(tutor_api, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(tutor_api, "audit_log", fake_audit_log)
    monkeypatch.setattr(tutor_api.registry, "get_student", lambda: object())
    monkeypatch.setattr(tutor_api.registry, "get_owner", lambda: None)

    response = asyncio.run(tutor_api.tutor_reply(body, settings))

    assert response.ok is True
    assert response.booking_id == "booking-1"
    assert response.escalation_id == 22
    assert calls["resolved_id"] == 22


def test_tutor_reply_rejects_mismatched_booking_and_escalation_id(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=None)
    body = tutor_api.ReplyRequest(
        booking_id="booking-2",
        escalation_id=22,
        text="Ответ преподавателя",
    )

    async def fake_load_by_id(*args, **kwargs) -> dict:
        return {"booking_id": "booking-1", "status": "pending", "escalation_id": 22}

    monkeypatch.setattr(tutor_api.escalations, "load_by_id", fake_load_by_id)

    try:
        asyncio.run(tutor_api.tutor_reply(body, settings))
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "booking_id does not match escalation_id"
    else:
        assert False, "Expected tutor_reply to reject mismatched reply identifiers"


def test_tutor_reply_supports_contact_only_escalation(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=None)
    body = tutor_api.ReplyRequest(escalation_id=33, text="Ответ по общему вопросу")
    delivered: dict[str, object] = {}
    metadata_updates: list[dict] = []

    async def fake_load_by_id(*args, **kwargs) -> dict:
        return {
            "booking_id": None,
            "contact_id": 91,
            "status": "pending",
            "escalation_id": 33,
        }

    async def fake_load_contact(*args, **kwargs) -> dict:
        return {"id": 91, "telegram_user_id": 555}

    async def fake_load_booking(*args, **kwargs):
        return None

    async def fake_send_student_message(**kwargs) -> bool:
        delivered.update(kwargs)
        return True

    async def fake_resolve_by_id(*args, **kwargs):
        return {"booking_id": None, "contact_id": 91, "status": "resolved", "escalation_id": 33}

    async def fake_update_metadata(*args, **kwargs) -> None:
        metadata_updates.append(kwargs)
        return None

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(tutor_api.escalations, "load_by_id", fake_load_by_id)
    monkeypatch.setattr(tutor_api.contacts, "load", fake_load_contact)
    monkeypatch.setattr(tutor_api.bookings, "load", fake_load_booking)
    monkeypatch.setattr(tutor_api.escalations, "resolve_by_id", fake_resolve_by_id)
    monkeypatch.setattr(tutor_api.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(tutor_api, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(tutor_api, "audit_log", fake_audit_log)
    monkeypatch.setattr(tutor_api.registry, "get_student", lambda: object())
    monkeypatch.setattr(tutor_api.registry, "get_owner", lambda: None)

    response = asyncio.run(tutor_api.tutor_reply(body, settings))

    assert response.ok is True
    assert response.booking_id is None
    assert delivered["contact_id"] == 91
    assert delivered["chat_id"] == 555
    assert metadata_updates
    assert metadata_updates[0]["contact_id"] == 91
    assert "automation_enabled" not in metadata_updates[0]


def test_list_escalations_parses_relevant_history_json(monkeypatch) -> None:
    import bridge.db as bridge_db

    async def fake_fetch(*args, **kwargs):
        return [
            {
                "id": 12,
                "booking_id": "booking-1",
                "contact_id": 91,
                "status": "pending",
                "reason": "policy_low_confidence",
                "question": "Как проходит занятие?",
                "summary": "Контекст: занятие. Причина: низкая уверенность ответа.",
                "relevant_history": '[{"role":"user","content":"Здравствуйте"}]',
                "draft_reply": "Занятие пройдёт онлайн.",
                "tutor_message_id": 100,
                "tutor_reply": None,
                "resolved_by": None,
                "created_at": datetime(2026, 4, 7, 8, 0, tzinfo=timezone.utc),
                "resolved_at": None,
                "event_title": "Пробный урок",
                "attendee": '{"name":"Student"}',
                "contact_name": "Student",
                "telegram_username": "student",
            }
        ]

    monkeypatch.setattr(
        bridge_db,
        "get_pool",
        lambda: SimpleNamespace(fetch=fake_fetch),
    )

    result = asyncio.run(tutor_api.list_escalations())

    assert result[0]["escalation_id"] == 12
    assert result[0]["student_name"] == "Student"
    assert result[0]["relevant_history"][0]["content"] == "Здравствуйте"
    assert result[0]["draft_reply"] == "Занятие пройдёт онлайн."


def test_set_contact_chat_mode_updates_contact_scoped_chat(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    saved: dict[str, object] = {}
    audited: dict[str, object] = {}
    chat = SimpleNamespace(
        mode=tutor_api.OperatingMode.AUTO,
        draft={"text": "pending"},
        automation_enabled=True,
        status="active",
        current_stage="new",
        assigned_human=None,
    )

    async def fake_load_contact(*args, **kwargs) -> dict:
        return {"id": 91, "name": "Ivan Petrov"}

    async def fake_load_chat_by_contact(*args, **kwargs):
        return chat

    async def fake_save_chat(*args, **kwargs) -> None:
        saved["chat"] = chat

    async def fake_audit_log(*args, **kwargs) -> None:
        audited.update(kwargs)

    monkeypatch.setattr(tutor_api.contacts, "load", fake_load_contact)
    monkeypatch.setattr(tutor_api.conversations, "load_chat_by_contact", fake_load_chat_by_contact)
    monkeypatch.setattr(tutor_api.conversations, "save_chat", fake_save_chat)
    monkeypatch.setattr(tutor_api, "audit_log", fake_audit_log)

    response = asyncio.run(
        tutor_api.set_contact_chat_mode(
            91,
            tutor_api.ModeRequest(mode=tutor_api.OperatingMode.MANUAL),
            settings,
        )
    )

    assert response["ok"] is True
    assert response["contact_id"] == 91
    assert response["mode"] == "manual"
    assert saved["chat"].mode == tutor_api.OperatingMode.MANUAL
    assert saved["chat"].automation_enabled is False
    assert saved["chat"].assigned_human == "tutor"
    assert audited["detail"]["contact_id"] == 91


def test_set_contact_chat_automation_updates_contact_scoped_chat(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    saved: dict[str, object] = {}
    audited: dict[str, object] = {}
    chat = SimpleNamespace(
        mode=tutor_api.OperatingMode.MANUAL,
        draft=None,
        automation_enabled=False,
        status="manual_takeover",
        current_stage="manual_takeover",
        assigned_human="tutor",
        escalation_reason="manual_mode",
    )

    async def fake_load_contact(*args, **kwargs) -> dict:
        return {"id": 91, "name": "Ivan Petrov"}

    async def fake_load_chat_by_contact(*args, **kwargs):
        return chat

    async def fake_save_chat(*args, **kwargs) -> None:
        saved["chat"] = chat

    async def fake_audit_log(*args, **kwargs) -> None:
        audited.update(kwargs)

    monkeypatch.setattr(tutor_api.contacts, "load", fake_load_contact)
    monkeypatch.setattr(tutor_api.conversations, "load_chat_by_contact", fake_load_chat_by_contact)
    monkeypatch.setattr(tutor_api.conversations, "save_chat", fake_save_chat)
    monkeypatch.setattr(tutor_api, "audit_log", fake_audit_log)

    response = asyncio.run(
        tutor_api.set_contact_chat_automation(
            91,
            tutor_api.ChatAutomationRequest(enabled=True),
            settings,
        )
    )

    assert response["ok"] is True
    assert response["contact_id"] == 91
    assert response["automation_enabled"] is True
    assert saved["chat"].mode == tutor_api.OperatingMode.SEMI_AUTO
    assert saved["chat"].assigned_human is None
    assert saved["chat"].escalation_reason is None
    assert audited["detail"]["contact_id"] == 91
