from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge.bot.handlers import messages, tutor
from bridge.state import OperatingMode
from telegram_adapter import templates


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


def test_tutor_reply_routes_by_booking_id_without_pending_escalation(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    reply_to_message = SimpleNamespace(
        message_id=321,
        text="Сообщение от студента\nID брони: booking-42",
        html_text=None,
        caption=None,
    )
    message = DummyMessage(text="Стоимость 1500 сом.", reply_to_message=reply_to_message)
    delivered: dict[str, object] = {}
    metadata_updates: list[tuple] = []
    audit_events: list[tuple[str, str, dict]] = []

    async def fake_find_pending_by_tutor_message(*args, **kwargs):
        return None

    async def fake_load_escalation(*args, **kwargs):
        return {"booking_id": "booking-42", "status": "resolved"}

    async def fake_load_booking(*args, **kwargs):
        return {"booking_id": "booking-42", "telegram_user_id": 777}

    async def fake_send_student_message(**kwargs) -> bool:
        delivered.update(kwargs)
        return True

    async def fake_update_metadata(*args, **kwargs) -> None:
        metadata_updates.append((args, kwargs))

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        audit_events.append((event_type, action, kwargs))

    async def fake_resolve(*args, **kwargs) -> None:
        raise AssertionError("resolve should not be called without a pending escalation")

    monkeypatch.setattr(tutor.approvals, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.escalations, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.escalations, "load", fake_load_escalation)
    monkeypatch.setattr(tutor.bookings, "load", fake_load_booking)
    monkeypatch.setattr(tutor, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(tutor.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(tutor, "audit_log", fake_audit_log)
    monkeypatch.setattr(tutor.escalations, "resolve", fake_resolve)
    monkeypatch.setattr(tutor.registry, "get_student", lambda: object())

    asyncio.run(tutor.on_tutor_reply(message, "tutor", settings))

    assert delivered["chat_id"] == 777
    assert delivered["booking_id"] == "booking-42"
    assert delivered["text"] == "Стоимость 1500 сом."
    assert message.answers == [templates.tutor_answer_sent()]
    assert metadata_updates
    assert audit_events[0][2]["detail"]["matched_pending_escalation"] is False


def test_manual_escalation_notice_includes_student_details() -> None:
    notice = templates.manual_escalation_notice(
        context_label="chat-stop",
        student_name="иван петров",
        booking_id="booking-42",
        question="Так какая стоимость занятия?",
        event_title="Встреча на 30 минут",
        start_time=None,
        student_email="student@example.com",
        student_phone="+996700000000",
        student_telegram="@joji",
        student_time_zone="Asia/Bishkek",
        student_telegram_user_id=555,
    )

    assert "<b>Сообщение от студента (chat-stop)</b>" in notice
    assert "<b>Telegram:</b> @joji" in notice
    assert "<b>Телефон:</b> +996700000000" in notice
    assert "<b>Email:</b> student@example.com" in notice
    assert "<b>Telegram user ID:</b> <code>555</code>" in notice
    assert "Так какая стоимость занятия?" in notice
