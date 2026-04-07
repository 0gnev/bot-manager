from __future__ import annotations

from bridge.policies.engine import evaluate_ai_response
from bridge.policies.models import (
    ConfidencePolicy,
    EscalationPolicy,
    ForbiddenReplyPolicy,
    PolicySet,
    ScopePolicy,
)
from bridge.state import OperatingMode


def _policy_set() -> PolicySet:
    return PolicySet(
        confidence=ConfidencePolicy(
            auto_send_min_confidence=0.85,
            clarify_send_min_confidence=0.75,
            approval_fallback_min_confidence=0.4,
        ),
        escalation=EscalationPolicy(
            stop_trigger_keywords={"human_request": ("преподавател", "человек")},
        ),
        forbidden_reply=ForbiddenReplyPolicy(
            patterns={"payment_confirmation": ("оплата подтвержд",)},
        ),
        scope=ScopePolicy(
            domain_keywords=("занят", "ссылка", "егэ", "информат"),
            obvious_off_topic_keywords=("пирог", "рецепт", "ингредиент"),
            generic_howto_prefixes=("как приготовить",),
        ),
    )


def test_policy_allows_safe_high_confidence_reply(monkeypatch) -> None:
    monkeypatch.setattr("bridge.policies.engine.load_policy_set", _policy_set)

    decision = evaluate_ai_response(
        response={"action": "answer", "content": "Здравствуйте", "confidence": 0.95},
        booking={"status": "active"},
        mode=OperatingMode.AUTO,
        student_text="Когда урок?",
        tutor_available=True,
    )

    assert decision.route == "send"
    assert decision.reason == "confidence_ok"


def test_policy_routes_low_confidence_to_approval(monkeypatch) -> None:
    monkeypatch.setattr("bridge.policies.engine.load_policy_set", _policy_set)

    decision = evaluate_ai_response(
        response={"action": "answer", "content": "Проверьте ссылку", "confidence": 0.55},
        booking={"status": "active"},
        mode=OperatingMode.AUTO,
        student_text="Где ссылка?",
        tutor_available=True,
    )

    assert decision.route == "approval"


def test_policy_escalates_stop_trigger(monkeypatch) -> None:
    monkeypatch.setattr("bridge.policies.engine.load_policy_set", _policy_set)

    decision = evaluate_ai_response(
        response={"action": "answer", "content": "Сейчас отвечу", "confidence": 0.99},
        booking={"status": "active"},
        mode=OperatingMode.AUTO,
        student_text="Позовите преподавателя",
        tutor_available=True,
    )

    assert decision.route == "escalate"
    assert decision.reason.startswith("stop_trigger:")


def test_policy_escalates_forbidden_reply(monkeypatch) -> None:
    monkeypatch.setattr("bridge.policies.engine.load_policy_set", _policy_set)

    decision = evaluate_ai_response(
        response={"action": "answer", "content": "Оплата подтверждена", "confidence": 0.99},
        booking={"status": "active"},
        mode=OperatingMode.AUTO,
        student_text="Спасибо",
        tutor_available=True,
    )

    assert decision.route == "escalate"
    assert decision.reason.startswith("forbidden_reply:")


def test_policy_blocks_obvious_off_topic_query(monkeypatch) -> None:
    monkeypatch.setattr("bridge.policies.engine.load_policy_set", _policy_set)

    decision = evaluate_ai_response(
        response={"action": "answer", "content": "Вот рецепт пирога", "confidence": 0.99},
        booking={"status": "active"},
        mode=OperatingMode.AUTO,
        student_text="Как приготовить яблочный пирог?",
        tutor_available=True,
    )

    assert decision.route == "block"
    assert decision.reason == "off_topic_query"
