"""
Runtime policy engine.

Evaluates AI output after inference and decides whether Bridge may send the
message automatically, must require tutor approval, should escalate, or must
block delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from bridge.state import OperatingMode

from bridge.policies.loader import load_policy_set
from bridge.policies.models import PolicySet

PolicyRoute = Literal["send", "approval", "escalate", "block"]

@dataclass(frozen=True)
class PolicyDecision:
    route: PolicyRoute
    reason: str


def evaluate_ai_response(
    *,
    response: dict,
    booking: dict,
    mode: OperatingMode,
    student_text: str,
    tutor_available: bool,
) -> PolicyDecision:
    """Evaluate model output against runtime policy rules."""
    policies = load_policy_set()

    if (booking.get("status") or "active") != "active":
        return PolicyDecision("block", "inactive_booking")

    action = str(response.get("action") or "answer").strip().lower()
    content = str(response.get("content") or "").strip()
    confidence = _coerce_confidence(response.get("confidence"))

    if action not in {"answer", "clarify", "escalate"}:
        return PolicyDecision("escalate", "unknown_action")

    stop_trigger = _detect_stop_trigger(student_text, policies)
    if stop_trigger:
        return PolicyDecision("escalate", f"stop_trigger:{stop_trigger}")

    forbidden_reason = _detect_forbidden_reply(content, policies)
    if forbidden_reason:
        return PolicyDecision("escalate", f"forbidden_reply:{forbidden_reason}")

    if action == "escalate":
        return PolicyDecision("escalate", "model_requested_escalation")

    if not content:
        return PolicyDecision("block", "empty_content")

    if mode == OperatingMode.SEMI_AUTO:
        return PolicyDecision("approval", "semi_auto_mode")

    if mode == OperatingMode.MANUAL:
        return PolicyDecision("block", "manual_mode")

    clarify_min = policies.confidence.clarify_send_min_confidence
    auto_min = policies.confidence.auto_send_min_confidence
    approval_floor = policies.confidence.approval_fallback_min_confidence

    required_confidence = clarify_min if action == "clarify" else auto_min
    if confidence >= required_confidence:
        return PolicyDecision("send", "confidence_ok")

    if tutor_available and confidence >= approval_floor:
        return PolicyDecision("approval", "low_confidence_requires_approval")

    return PolicyDecision("escalate", "low_confidence_escalation")


def _coerce_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _detect_stop_trigger(student_text: str, policies: PolicySet) -> str | None:
    text = (student_text or "").lower()
    stop_triggers = policies.escalation.stop_trigger_keywords

    for trigger, keywords in stop_triggers.items():
        for keyword in keywords:
            if keyword.lower() in text:
                return str(trigger)
    return None


def _detect_forbidden_reply(content: str, policies: PolicySet) -> str | None:
    lowered = content.lower()
    for reason, patterns in policies.forbidden_reply.patterns.items():
        if any(pattern in lowered for pattern in patterns):
            return reason
    return None
