from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge.bot.handlers import messages, tutor
from bridge.state import OperatingMode


class DummyMessage:
    def __init__(self, text: str = "", reply_to_message: SimpleNamespace | None = None) -> None:
        self.text = text
        self.reply_to_message = reply_to_message
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append(text)


def test_policy_escalation_forwards_original_student_text(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=1)
    booking = {"booking_id": "booking-1", "status": "active"}
    message = DummyMessage(text="Сколько стоит занятие?")
    captured: dict[str, str] = {}

    async def fake_load_chat(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.AUTO)

    async def fake_update_metadata(*args, **kwargs) -> None:
        return None

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    async def fake_escalate(message_obj, booking_obj, question, settings_obj) -> None:
        captured["question"] = question

    monkeypatch.setattr(messages.conversations, "load_chat", fake_load_chat)
    monkeypatch.setattr(messages.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(messages, "audit_log", fake_audit_log)
    monkeypatch.setattr(
        messages,
        "evaluate_ai_response",
        lambda **kwargs: SimpleNamespace(route="escalate", reason="model_requested_escalation"),
    )
    monkeypatch.setattr(messages, "escalate", fake_escalate)

    asyncio.run(
        messages._apply_policy_result(
            message=message,
            booking=booking,
            response={
                "action": "escalate",
                "content": "Не могу ответить прямо сейчас — передаю преподавателю.",
                "confidence": 0.0,
            },
            settings=settings,
            booking_id="booking-1",
            student_text="Сколько стоит занятие?",
        )
    )

    assert captured["question"] == "Сколько стоит занятие?"


def test_tutor_reply_reports_unmatched_escalation(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    reply_to_message = SimpleNamespace(
        message_id=321,
        text="Сообщение без активной эскалации",
        html_text=None,
        caption=None,
    )
    message = DummyMessage(text="Ответ преподавателя", reply_to_message=reply_to_message)

    async def fake_find_pending_by_tutor_message(*args, **kwargs):
        return None

    async def fake_load(*args, **kwargs):
        return None

    monkeypatch.setattr(tutor.approvals, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.escalations, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.escalations, "load", fake_load)

    asyncio.run(tutor.on_tutor_reply(message, "tutor", settings))

    assert message.answers == [
        "Не удалось сопоставить ответ с активным вопросом. "
        "Ответьте реплаем на последнее сообщение с ID брони."
    ]
