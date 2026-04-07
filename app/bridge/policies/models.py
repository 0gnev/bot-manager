"""
Normalized policy models shared by prompt rendering and runtime enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResponseActionPolicy:
    name: str
    description: str


@dataclass(frozen=True)
class ConfidencePolicy:
    auto_send_min_confidence: float = 0.85
    clarify_send_min_confidence: float = 0.75
    approval_fallback_min_confidence: float = 0.40
    rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class EscalationPolicy:
    escalate_when: tuple[str, ...] = ()
    stop_trigger_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class TonePolicy:
    language: str = "Russian"
    rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForbiddenReplyPolicy:
    patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopePolicy:
    domain_keywords: tuple[str, ...] = ()
    obvious_off_topic_keywords: tuple[str, ...] = ()
    generic_howto_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicySet:
    response_actions: tuple[ResponseActionPolicy, ...] = ()
    output_format: str = ""
    confidence: ConfidencePolicy = ConfidencePolicy()
    escalation: EscalationPolicy = EscalationPolicy()
    tone: TonePolicy = TonePolicy()
    forbidden_reply: ForbiddenReplyPolicy = ForbiddenReplyPolicy()
    scope: ScopePolicy = ScopePolicy()
