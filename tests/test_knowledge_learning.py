from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge import knowledge_learning


def test_capture_skips_private_candidate(monkeypatch) -> None:
    settings = SimpleNamespace(
        state_path="/tmp/state",
        knowledge_path="/tmp/knowledge",
        tutor_chat_id=123,
    )
    created: list[dict] = []

    class FakeClient:
        def __init__(self, settings_obj) -> None:
            self.settings = settings_obj

        async def propose_knowledge_candidate(self, **kwargs):
            return {"should_save": False, "reason": "Частный booking-specific случай."}

    async def fake_load_booking(*args, **kwargs):
        return {"booking_id": "booking-1", "title": "Урок"}

    async def fake_load_contact(*args, **kwargs):
        return {"id": 7, "name": "Ivan Petrov"}

    async def fake_create(*args, **kwargs):
        created.append(kwargs)
        return {"id": 1}

    async def fake_audit_log(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge_learning, "OpenclawClient", FakeClient)
    monkeypatch.setattr(knowledge_learning.bookings, "load", fake_load_booking)
    monkeypatch.setattr(knowledge_learning.contacts, "load", fake_load_contact)
    monkeypatch.setattr(knowledge_learning.suggestions_state, "create", fake_create)
    monkeypatch.setattr(knowledge_learning, "audit_log", fake_audit_log)
    monkeypatch.setattr(knowledge_learning.registry, "get_owner", lambda: object())

    result = asyncio.run(
        knowledge_learning.capture(
            settings=settings,
            source_kind="approval",
            booking_id="booking-1",
            contact_id=7,
            approval_id="appr-1",
            source_question="Где моя ссылка?",
            final_answer="Вот ваша ссылка на конкретную встречу ...",
        )
    )

    assert result is None
    assert created == []


def test_approve_suggestion_writes_knowledge_file(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        state_path="/tmp/state",
        knowledge_path=str(tmp_path),
    )
    executed: list[tuple] = []

    async def fake_load(*args, **kwargs):
        return {
            "id": 17,
            "status": "pending",
            "booking_id": None,
            "contact_id": 7,
            "title": "Стоимость занятия",
            "content_markdown": "# Стоимость занятия\n\nСтоимость занятия — 3000 рублей.",
            "suggested_file_path": "approved/stoimost-zanyatiya.md",
        }

    async def fake_resolve(*args, **kwargs):
        return {"status": "saved", "knowledge_file_path": kwargs["knowledge_file_path"]}

    async def fake_audit_log(*args, **kwargs):
        return None

    class FakePool:
        async def execute(self, *args):
            executed.append(args)
            return None

    monkeypatch.setattr(knowledge_learning.suggestions_state, "load", fake_load)
    monkeypatch.setattr(knowledge_learning.suggestions_state, "resolve", fake_resolve)
    monkeypatch.setattr(knowledge_learning, "audit_log", fake_audit_log)
    monkeypatch.setattr(knowledge_learning, "get_pool", lambda: FakePool())

    relative_path = asyncio.run(knowledge_learning.approve_suggestion(17, settings))

    assert relative_path == "approved/stoimost-zanyatiya.md"
    assert (tmp_path / "approved" / "stoimost-zanyatiya.md").read_text(encoding="utf-8").startswith(
        "# Стоимость занятия"
    )
    assert executed
