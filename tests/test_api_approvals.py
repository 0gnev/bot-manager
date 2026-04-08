from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from bridge.api import approvals as approvals_api
from bridge.approvals import handler as approval_handler


def test_revise_endpoint_keeps_approval_pending(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    body = approvals_api.EditApprovalRequest(text="Оставь только стоимость занятия")
    calls: list[tuple[str, str]] = []
    approvals_seen = [
        {
            "approval_id": "appr-1",
            "status": "pending",
            "draft_content": "Стоимость занятия — 3 рубля. Хотите ссылку?",
        },
        {
            "approval_id": "appr-1",
            "status": "pending",
            "draft_content": "Стоимость занятия — 3 рубля.",
        },
    ]

    async def fake_get_approval(*args, **kwargs) -> dict:
        return approvals_seen.pop(0)

    async def fake_revise_pending_approval(approval_id: str, instruction: str, settings_obj) -> dict:
        calls.append((approval_id, instruction))
        return {
            "decision": "revise",
            "content": "Стоимость занятия — 3 рубля.",
            "approval_id": approval_id,
        }

    monkeypatch.setattr(approvals_api.approvals, "get_approval", fake_get_approval)
    monkeypatch.setattr(approvals_api.approval_handler, "revise_pending_approval", fake_revise_pending_approval)

    result = asyncio.run(approvals_api.revise_endpoint("appr-1", body, settings))

    assert calls == [("appr-1", "Оставь только стоимость занятия")]
    assert result == {
        "ok": True,
        "approval_id": "appr-1",
        "status": "pending",
        "draft_content": "Стоимость занятия — 3 рубля.",
    }


def test_revise_endpoint_rejects_resolved_approval(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    body = approvals_api.EditApprovalRequest(text="Исправь ответ")

    async def fake_get_approval(*args, **kwargs) -> dict:
        return {
            "approval_id": "appr-1",
            "status": "approved",
        }

    monkeypatch.setattr(approvals_api.approvals, "get_approval", fake_get_approval)

    try:
        asyncio.run(approvals_api.revise_endpoint("appr-1", body, settings))
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "Approval already resolved"
    else:
        assert False, "Expected revise endpoint to reject resolved approval"


def test_revise_pending_approval_never_direct_sends_without_explicit_send(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=77)
    calls: dict[str, object] = {}

    async def fake_get_approval(*args, **kwargs) -> dict:
        return {
            "approval_id": "appr-1",
            "status": "pending",
            "booking_id": "booking-1",
            "contact_id": 9,
            "action": "answer",
            "draft_content": "Старый черновик",
            "tutor_message_id": 10,
            "confidence": 0.7,
        }

    async def fake_load_booking(*args, **kwargs) -> dict:
        return {"booking_id": "booking-1", "contact_id": 9}

    async def fake_load_contact(*args, **kwargs) -> dict:
        return {"id": 9, "name": "Ivan Petrov"}

    async def fake_find_all_by_contact(*args, **kwargs) -> list[dict]:
        return [{"booking_id": "booking-1"}]

    async def fake_load_history(*args, **kwargs) -> list[dict]:
        return [{"role": "user", "content": "Сколько стоит занятие?"}]

    class FakeOwnerBot:
        async def edit_message_reply_markup(self, **kwargs):
            calls["cleared"] = kwargs

        async def send_message(self, chat_id: int, text: str, reply_markup=None):
            calls["notice"] = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
            return SimpleNamespace(message_id=11)

    class FakeClient:
        def __init__(self, settings_obj) -> None:
            self.settings = settings_obj

        async def revise_approval_draft(self, **kwargs):
            calls["revision_request"] = kwargs
            return {
                "decision": "send",
                "content": "Стоимость занятия — 3 рубля.",
                "confidence": 0.88,
            }

    async def fake_update_pending_draft(*args, **kwargs):
        calls["updated"] = kwargs
        return {"approval_id": "appr-1", "status": "pending", "draft_content": "Стоимость занятия — 3 рубля."}

    async def fake_update_metadata(*args, **kwargs):
        calls["metadata"] = kwargs
        return None

    async def fake_audit_log(*args, **kwargs):
        calls["audited"] = True
        return None

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("edit_and_approve must not be called on plain revise flow")

    monkeypatch.setattr(approval_handler.approvals, "get_approval", fake_get_approval)
    monkeypatch.setattr(approval_handler.bookings, "load", fake_load_booking)
    monkeypatch.setattr(approval_handler.contacts, "load", fake_load_contact)
    monkeypatch.setattr(approval_handler.bookings, "find_all_by_contact", fake_find_all_by_contact)
    monkeypatch.setattr(approval_handler.conversations, "load", fake_load_history)
    monkeypatch.setattr(approval_handler.registry, "get_owner", lambda: FakeOwnerBot())
    monkeypatch.setattr(approval_handler, "OpenclawClient", FakeClient)
    monkeypatch.setattr(approval_handler.approvals, "update_pending_draft", fake_update_pending_draft)
    monkeypatch.setattr(approval_handler.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(approval_handler, "audit_log", fake_audit_log)
    monkeypatch.setattr(approval_handler, "edit_and_approve", fail_if_called)

    result = asyncio.run(
        approval_handler.revise_pending_approval(
            "appr-1",
            "Оставь только стоимость занятия",
            settings,
        )
    )

    assert result is not None
    assert result["decision"] == "revise"
    assert result["content"] == "Стоимость занятия — 3 рубля."
    assert calls["notice"]["chat_id"] == 77


def test_approve_schedules_knowledge_capture(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    captured: list[dict[str, object]] = []

    async def fake_get_approval(*args, **kwargs) -> dict:
        return {
            "approval_id": "appr-1",
            "status": "pending",
            "booking_id": "booking-1",
            "contact_id": 9,
            "student_chat_id": 123,
            "student_question": "Сколько стоит занятие?",
            "draft_content": "Стоимость занятия — 3000 рублей.",
            "confidence": 0.8,
        }

    async def fake_send_student_message(**kwargs) -> bool:
        return True

    async def fake_resolve_approval(*args, **kwargs):
        return {"approval_id": "appr-1", "status": "approved"}

    async def fake_update_metadata(*args, **kwargs):
        return None

    async def fake_audit_log(*args, **kwargs):
        return None

    monkeypatch.setattr(approval_handler.approvals, "get_approval", fake_get_approval)
    monkeypatch.setattr(approval_handler, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(approval_handler.approvals, "resolve_approval", fake_resolve_approval)
    monkeypatch.setattr(approval_handler.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(approval_handler, "audit_log", fake_audit_log)
    monkeypatch.setattr(approval_handler.registry, "get_student", lambda: object())
    monkeypatch.setattr(
        approval_handler,
        "schedule_capture",
        lambda **kwargs: captured.append(kwargs),
    )

    result = asyncio.run(approval_handler.approve("appr-1", settings))

    assert result is True
    assert captured == [
        {
            "settings": settings,
            "source_kind": "approval",
            "booking_id": "booking-1",
            "contact_id": 9,
            "approval_id": "appr-1",
            "source_question": "Сколько стоит занятие?",
            "final_answer": "Стоимость занятия — 3000 рублей.",
        }
    ]


def test_edit_and_approve_schedules_knowledge_capture(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    captured: list[dict[str, object]] = []

    async def fake_get_approval(*args, **kwargs) -> dict:
        return {
            "approval_id": "appr-1",
            "status": "pending",
            "booking_id": None,
            "contact_id": 9,
            "student_chat_id": 123,
            "student_question": "Как проходит вводный урок?",
            "draft_content": "Черновик",
            "confidence": 0.8,
        }

    async def fake_send_student_message(**kwargs) -> bool:
        return True

    async def fake_resolve_approval(*args, **kwargs):
        return {"approval_id": "appr-1", "status": "edited"}

    async def fake_update_metadata(*args, **kwargs):
        return None

    async def fake_audit_log(*args, **kwargs):
        return None

    monkeypatch.setattr(approval_handler.approvals, "get_approval", fake_get_approval)
    monkeypatch.setattr(approval_handler, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(approval_handler.approvals, "resolve_approval", fake_resolve_approval)
    monkeypatch.setattr(approval_handler.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(approval_handler, "audit_log", fake_audit_log)
    monkeypatch.setattr(approval_handler.registry, "get_student", lambda: object())
    monkeypatch.setattr(
        approval_handler,
        "schedule_capture",
        lambda **kwargs: captured.append(kwargs),
    )

    result = asyncio.run(
        approval_handler.edit_and_approve(
            "appr-1",
            "Вводный урок проходит онлайн и включает разбор текущего уровня.",
            settings,
        )
    )

    assert result is True
    assert captured == [
        {
            "settings": settings,
            "source_kind": "approval",
            "booking_id": None,
            "contact_id": 9,
            "approval_id": "appr-1",
            "source_question": "Как проходит вводный урок?",
            "final_answer": "Вводный урок проходит онлайн и включает разбор текущего уровня.",
        }
    ]
