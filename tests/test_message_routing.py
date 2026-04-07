from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge.bot.handlers import messages, tutor
from bridge.bot.filters import StudentBotFilter, TutorBotFilter
from bridge.bot import registry
from bridge.escalation import handler as escalation_handler
from bridge.state import OperatingMode
from telegram_adapter import templates


class DummyMessage:
    def __init__(self, text: str = "", reply_to_message: SimpleNamespace | None = None) -> None:
        self.text = text
        self.reply_to_message = reply_to_message
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append(text)


class DummyStudentMessage(DummyMessage):
    def __init__(self, text: str = "") -> None:
        super().__init__(text=text)
        self.from_user = SimpleNamespace(id=321, username="joji5213", full_name="Ivan Petrov")
        self.chat = SimpleNamespace(id=321)
        self.bot = object()


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

    async def fake_escalate(message_obj, booking_obj, contact_obj, question, settings_obj, **kwargs) -> None:
        captured["question"] = question

    monkeypatch.setattr(messages.conversations, "load_chat_by_contact", fake_load_chat)
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
            contact={"id": 9, "name": "Ivan Petrov"},
            contact_id=9,
            student_text="Сколько стоит занятие?",
        )
    )

    assert captured["question"] == "Сколько стоит занятие?"


def test_student_message_without_booking_routes_via_contact(monkeypatch) -> None:
    settings = SimpleNamespace(
        state_path="/tmp/state",
        knowledge_path="/tmp/knowledge",
        tutor_chat_id=1,
    )
    message = DummyStudentMessage("Можно задать общий вопрос?")
    delivered: dict[str, object] = {}

    async def fake_resolve_contact_context(*args, **kwargs):
        return ({"id": 41, "name": "Ivan Petrov"}, None, [])

    async def fake_append(*args, **kwargs):
        return None

    async def fake_load_chat_by_contact(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.AUTO, automation_enabled=True)

    async def fake_update_metadata(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.AUTO, automation_enabled=True)

    async def fake_load(*args, **kwargs):
        return [{"role": "user", "content": "Можно задать общий вопрос?"}]

    async def fake_load_controls(*args, **kwargs):
        return {"global_automation_enabled": True}

    async def fake_search(*args, **kwargs):
        return []

    async def fake_send_student_message(**kwargs):
        delivered.update(kwargs)
        return True

    async def fake_audit_log(*args, **kwargs):
        return None

    class FakeClient:
        def __init__(self, settings_obj) -> None:
            self.settings = settings_obj

        async def chat(self, **kwargs):
            return {"action": "answer", "content": "Да, конечно.", "confidence": 0.95}

    monkeypatch.setattr(messages, "_resolve_contact_context", fake_resolve_contact_context)
    monkeypatch.setattr(messages.conversations, "append", fake_append)
    monkeypatch.setattr(messages.conversations, "load_chat_by_contact", fake_load_chat_by_contact)
    monkeypatch.setattr(messages.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(messages.conversations, "load", fake_load)
    monkeypatch.setattr(messages, "load_controls", fake_load_controls)
    monkeypatch.setattr(messages, "knowledge_search", fake_search)
    monkeypatch.setattr(messages, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(messages, "audit_log", fake_audit_log)
    monkeypatch.setattr(messages, "OpenclawClient", FakeClient)

    asyncio.run(messages.on_text(message, "student", settings))

    assert delivered["booking_id"] is None
    assert delivered["contact_id"] == 41
    assert delivered["text"] == "Да, конечно."


def test_semi_auto_without_booking_submits_contact_only_approval(monkeypatch) -> None:
    settings = SimpleNamespace(
        state_path="/tmp/state",
        knowledge_path="/tmp/knowledge",
        tutor_chat_id=1,
    )
    message = DummyStudentMessage("Можно ли перенести тему занятия?")
    submitted: dict[str, object] = {}

    async def fake_resolve_contact_context(*args, **kwargs):
        return ({"id": 41, "name": "Ivan Petrov"}, None, [])

    async def fake_append(*args, **kwargs):
        return None

    async def fake_load_chat_by_contact(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.SEMI_AUTO, automation_enabled=True)

    async def fake_update_metadata(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.SEMI_AUTO, automation_enabled=True)

    async def fake_load(*args, **kwargs):
        return [{"role": "user", "content": "Можно ли перенести тему занятия?"}]

    async def fake_load_controls(*args, **kwargs):
        return {"global_automation_enabled": True}

    async def fake_search(*args, **kwargs):
        return []

    async def fake_submit_for_approval(**kwargs):
        submitted.update(kwargs)
        return {"approval_id": "appr-1"}

    async def fake_audit_log(*args, **kwargs):
        return None

    class FakeClient:
        def __init__(self, settings_obj) -> None:
            self.settings = settings_obj

        async def chat(self, **kwargs):
            return {"action": "answer", "content": "Да, можно обсудить перенос.", "confidence": 0.75}

    monkeypatch.setattr(messages, "_resolve_contact_context", fake_resolve_contact_context)
    monkeypatch.setattr(messages.conversations, "append", fake_append)
    monkeypatch.setattr(messages.conversations, "load_chat_by_contact", fake_load_chat_by_contact)
    monkeypatch.setattr(messages.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(messages.conversations, "load", fake_load)
    monkeypatch.setattr(messages, "load_controls", fake_load_controls)
    monkeypatch.setattr(messages, "knowledge_search", fake_search)
    monkeypatch.setattr(messages, "submit_for_approval", fake_submit_for_approval)
    monkeypatch.setattr(messages, "audit_log", fake_audit_log)
    monkeypatch.setattr(messages, "OpenclawClient", FakeClient)

    asyncio.run(messages.on_text(message, "student", settings))

    assert submitted["booking_id"] is None
    assert submitted["contact_id"] == 41
    assert submitted["student_chat_id"] == 321
    assert message.answers == ["Ваш преподаватель проверит ответ и отправит его вручную."]


def test_stale_chat_with_pending_escalation_is_auto_resumed(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    updated: dict[str, object] = {}

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("Pending queues must not block stale auto-resume")

    async def fake_update_metadata(*args, **kwargs):
        updated.update(kwargs)
        return SimpleNamespace(
            mode=OperatingMode.AUTO,
            automation_enabled=True,
            status="active",
            current_stage="automation_auto_resumed",
        )

    monkeypatch.setattr(messages.escalations, "list_pending", fail_if_called)
    monkeypatch.setattr(messages.conversations, "update_metadata", fake_update_metadata)

    resumed = asyncio.run(
        messages._resume_stale_automation_if_needed(
            settings,
            booking_id="booking-1",
            contact_id=41,
            chat=SimpleNamespace(
                mode=OperatingMode.AUTO,
                automation_enabled=False,
            ),
        )
    )

    assert resumed.automation_enabled is True
    assert updated["booking_id"] == "booking-1"
    assert updated["contact_id"] == 41
    assert updated["status"] == "active"
    assert updated["current_stage"] == "automation_auto_resumed"


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


def test_tutor_reply_to_approval_revises_draft(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    reply_to_message = SimpleNamespace(message_id=321, text="Черновик для проверки")
    message = DummyMessage(text="Отправь только стоимость занятия", reply_to_message=reply_to_message)
    calls: list[tuple[str, str]] = []

    async def fake_find_pending_by_tutor_message(*args, **kwargs):
        return {"approval_id": "appr-1", "booking_id": "booking-42", "status": "pending"}

    async def fake_revise_pending_approval(approval_id: str, instruction: str, settings_obj):
        calls.append((approval_id, instruction))
        return {"decision": "revise", "content": "Стоимость занятия — 3 рубля."}

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("Direct approve path must not be used for plain tutor instructions")

    monkeypatch.setattr(tutor.approvals, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.approval_handler, "revise_pending_approval", fake_revise_pending_approval)
    monkeypatch.setattr(tutor.approval_handler, "edit_and_approve", fail_if_called)

    asyncio.run(tutor.on_tutor_reply(message, "tutor", settings))

    assert calls == [("appr-1", "Отправь только стоимость занятия")]
    assert message.answers == ["Черновик обновлён. Проверьте новый вариант выше."]


def test_tutor_reply_to_approval_send_command_sends_directly(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    reply_to_message = SimpleNamespace(message_id=321, text="Черновик для проверки")
    message = DummyMessage(text="/send Стоимость занятия — 3 рубля.", reply_to_message=reply_to_message)
    calls: list[tuple[str, str]] = []

    async def fake_find_pending_by_tutor_message(*args, **kwargs):
        return {"approval_id": "appr-1", "booking_id": "booking-42", "status": "pending"}

    async def fake_edit_and_approve(approval_id: str, new_content: str, settings_obj):
        calls.append((approval_id, new_content))
        return True

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("Revision path must not be used for /send replies")

    monkeypatch.setattr(tutor.approvals, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.approval_handler, "edit_and_approve", fake_edit_and_approve)
    monkeypatch.setattr(tutor.approval_handler, "revise_pending_approval", fail_if_called)

    asyncio.run(tutor.on_tutor_reply(message, "tutor", settings))

    assert calls == [("appr-1", "Стоимость занятия — 3 рубля.")]
    assert message.answers == ["Ответ отправлен студенту."]


def test_policy_block_off_topic_returns_scope_notice(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    message = DummyMessage(text="Как приготовить яблочный пирог?")

    async def fake_load_chat_by_contact(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.AUTO)

    async def fake_update_metadata(*args, **kwargs):
        return None

    async def fake_audit_log(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "audit_log", fake_audit_log)
    monkeypatch.setattr(messages.conversations, "load_chat_by_contact", fake_load_chat_by_contact)
    monkeypatch.setattr(messages.conversations, "update_metadata", fake_update_metadata)

    asyncio.run(
        messages._apply_policy_result(
            message=message,
            booking={"booking_id": "booking-1", "status": "active"},
            contact={"id": 9, "name": "Ivan Petrov"},
            response={"action": "answer", "content": "Вот рецепт", "confidence": 0.99},
            settings=settings,
            booking_id="booking-1",
            contact_id=9,
            student_text="Как приготовить яблочный пирог?",
        )
    )

    assert message.answers == [templates.out_of_scope_question()]


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


def test_tutor_reply_routes_contact_only_escalation(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    reply_to_message = SimpleNamespace(
        message_id=400,
        text="Сообщение от студента без записи",
        html_text=None,
        caption=None,
    )
    message = DummyMessage(text="Ответ без брони", reply_to_message=reply_to_message)
    delivered: dict[str, object] = {}

    async def fake_find_pending_by_tutor_message(*args, **kwargs):
        return {"booking_id": None, "contact_id": 7, "status": "pending", "escalation_id": 55}

    async def fake_load_contact(*args, **kwargs):
        return {"id": 7, "telegram_user_id": 900}

    async def fake_send_student_message(**kwargs) -> bool:
        delivered.update(kwargs)
        return True

    async def fake_update_metadata(*args, **kwargs) -> None:
        return None

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    async def fake_resolve_by_id(*args, **kwargs):
        return {"booking_id": None, "contact_id": 7, "status": "resolved", "escalation_id": 55}

    async def fake_no_approval(*args, **kwargs):
        return None

    async def fake_no_booking(*args, **kwargs):
        return None

    monkeypatch.setattr(tutor.approvals, "find_pending_by_tutor_message", fake_no_approval)
    monkeypatch.setattr(tutor.escalations, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.contacts, "load", fake_load_contact)
    monkeypatch.setattr(tutor.bookings, "load", fake_no_booking)
    monkeypatch.setattr(tutor, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(tutor.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(tutor, "audit_log", fake_audit_log)
    monkeypatch.setattr(tutor.escalations, "resolve_by_id", fake_resolve_by_id)
    monkeypatch.setattr(tutor.registry, "get_student", lambda: object())

    asyncio.run(tutor.on_tutor_reply(message, "tutor", settings))

    assert delivered["chat_id"] == 900
    assert delivered["contact_id"] == 7
    assert delivered["booking_id"] is None
    assert message.answers == [templates.tutor_answer_sent()]


def test_escalation_keeps_contact_automation_enabled(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state", tutor_chat_id=123)
    message = DummyStudentMessage("Нужен преподаватель")
    metadata_updates: list[dict] = []

    class FakeOwnerBot:
        async def send_message(self, chat_id: int, text: str):
            return SimpleNamespace(message_id=555)

    async def fake_create(*args, **kwargs):
        return {"escalation_id": 1}

    async def fake_update_metadata(*args, **kwargs):
        metadata_updates.append(kwargs)
        return None

    async def fake_audit_log(*args, **kwargs):
        return None

    async def fake_load_chat_by_contact(*args, **kwargs):
        return SimpleNamespace(mode=OperatingMode.AUTO, confidence=0.42)

    async def fake_load_history(*args, **kwargs):
        return [
            {"role": "user", "content": "Здравствуйте", "ts": "2026-04-07T08:00:00+00:00"},
            {"role": "assistant", "content": "Добрый день", "ts": "2026-04-07T08:01:00+00:00"},
            {"role": "user", "content": "Нужен преподаватель", "ts": "2026-04-07T08:02:00+00:00"},
        ]

    monkeypatch.setattr(escalation_handler.registry, "get_owner", lambda: FakeOwnerBot())
    monkeypatch.setattr(escalation_handler.escalations, "create", fake_create)
    monkeypatch.setattr(escalation_handler.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(escalation_handler.conversations, "load_chat_by_contact", fake_load_chat_by_contact)
    monkeypatch.setattr(escalation_handler.conversations, "load", fake_load_history)
    monkeypatch.setattr(escalation_handler, "audit_log", fake_audit_log)

    asyncio.run(
        escalation_handler.escalate(
            message,
            None,
            {"id": 7, "name": "Ivan Petrov", "telegram_user_id": 321},
            "Нужен преподаватель",
            settings,
        )
    )

    assert metadata_updates
    assert metadata_updates[0]["contact_id"] == 7
    assert metadata_updates[0]["current_stage"] == "awaiting_tutor_reply"
    assert "automation_enabled" not in metadata_updates[0]


def test_escalation_notice_includes_summary_history_and_draft() -> None:
    notice = templates.escalation_notice(
        student_name="иван петров",
        booking_id="booking-42",
        question="Как будет проходить занятие?",
        event_title="Встреча на 30 минут",
        start_time=None,
        summary="Контекст: Встреча на 30 минут. Причина: низкая уверенность ответа.",
        relevant_history=[
            {"role": "user", "content": "Здравствуйте"},
            {"role": "assistant", "content": "Добрый день"},
        ],
        draft_reply="Занятие пройдёт онлайн по ссылке из подтверждения.",
    )

    assert "<b>Сводка:</b>" in notice
    assert "Причина: низкая уверенность ответа." in notice
    assert "<b>Недавний диалог:</b>" in notice
    assert "<b>Студент:</b> Здравствуйте" in notice
    assert "<b>Бот:</b> Добрый день" in notice
    assert "<b>Черновик ответа:</b>" in notice
    assert "Занятие пройдёт онлайн по ссылке из подтверждения." in notice


def test_tutor_reply_keeps_contact_automation_enabled(monkeypatch) -> None:
    settings = SimpleNamespace(state_path="/tmp/state")
    reply_to_message = SimpleNamespace(
        message_id=400,
        text="Сообщение от студента без записи",
        html_text=None,
        caption=None,
    )
    message = DummyMessage(text="Ответ без брони", reply_to_message=reply_to_message)
    metadata_updates: list[dict] = []

    async def fake_find_pending_by_tutor_message(*args, **kwargs):
        return {"booking_id": None, "contact_id": 7, "status": "pending", "escalation_id": 55}

    async def fake_load_contact(*args, **kwargs):
        return {"id": 7, "telegram_user_id": 900}

    async def fake_send_student_message(**kwargs) -> bool:
        return True

    async def fake_update_metadata(*args, **kwargs) -> None:
        metadata_updates.append(kwargs)
        return None

    async def fake_audit_log(*args, **kwargs) -> None:
        return None

    async def fake_resolve_by_id(*args, **kwargs):
        return {"booking_id": None, "contact_id": 7, "status": "resolved", "escalation_id": 55}

    async def fake_no_booking(*args, **kwargs):
        return None

    async def fake_no_approval(*args, **kwargs):
        return None

    monkeypatch.setattr(tutor.approvals, "find_pending_by_tutor_message", fake_no_approval)
    monkeypatch.setattr(tutor.escalations, "find_pending_by_tutor_message", fake_find_pending_by_tutor_message)
    monkeypatch.setattr(tutor.contacts, "load", fake_load_contact)
    monkeypatch.setattr(tutor.bookings, "load", fake_no_booking)
    monkeypatch.setattr(tutor, "send_student_message", fake_send_student_message)
    monkeypatch.setattr(tutor.conversations, "update_metadata", fake_update_metadata)
    monkeypatch.setattr(tutor, "audit_log", fake_audit_log)
    monkeypatch.setattr(tutor.escalations, "resolve_by_id", fake_resolve_by_id)
    monkeypatch.setattr(tutor.registry, "get_student", lambda: object())

    asyncio.run(tutor.on_tutor_reply(message, "tutor", settings))

    assert metadata_updates
    assert metadata_updates[0]["contact_id"] == 7
    assert metadata_updates[0]["current_stage"] == "tutor_reply_sent"
    assert "automation_enabled" not in metadata_updates[0]


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


def test_student_bot_filter_matches_only_student_bot() -> None:
    registry.register(
        SimpleNamespace(token="student-token"),
        owner_token="owner-token",
        owner_bot=SimpleNamespace(token="owner-token"),
    )
    flt = StudentBotFilter()

    assert asyncio.run(flt(bot=SimpleNamespace(token="student-token"))) is True
    assert asyncio.run(flt(bot=SimpleNamespace(token="owner-token"))) is False


def test_tutor_bot_filter_matches_only_owner_bot() -> None:
    registry.register(
        SimpleNamespace(token="student-token"),
        owner_token="owner-token",
        owner_bot=SimpleNamespace(token="owner-token"),
    )
    flt = TutorBotFilter()

    assert asyncio.run(flt(bot=SimpleNamespace(token="owner-token"))) is True
    assert asyncio.run(flt(bot=SimpleNamespace(token="student-token"))) is False
