"""Load policy YAML files and normalize them for prompts and enforcement."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from bridge.policies.models import (
    ConfidencePolicy,
    EscalationPolicy,
    ForbiddenReplyPolicy,
    PolicySet,
    ResponseActionPolicy,
    TonePolicy,
)

logger = logging.getLogger(__name__)

_cache: dict[str, object] = {}
_mtimes: dict[str, float] = {}


def load_policies(path: str = "config/policies") -> dict:
    """Load all YAML files from *path*, returning a merged dict keyed by stem."""
    policies_dir = Path(path)
    if not policies_dir.is_dir():
        logger.warning("Policies directory not found: %s", path)
        return {}

    result: dict = {}
    for fp in sorted(policies_dir.glob("*.yaml")):
        mtime = fp.stat().st_mtime
        key = fp.stem
        if key in _cache and _mtimes.get(key) == mtime:
            result[key] = _cache[key]
            continue
        try:
            data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.error("Failed to load policy %s: %s", fp, exc)
            continue
        _cache[key] = data
        _mtimes[key] = mtime
        result[key] = data
    return result


def reload_policies(path: str = "config/policies") -> dict:
    """Force-clear cache and reload all policies."""
    _cache.clear()
    _mtimes.clear()
    return load_policies(path)


def load_policy_set(path: str = "config/policies") -> PolicySet:
    """Load YAML policies into a normalized shared policy model."""
    policies = load_policies(path)

    response_actions_raw = policies.get("response_actions") or {}
    actions = tuple(
        ResponseActionPolicy(
            name=name,
            description=str((info or {}).get("description", "")).strip(),
        )
        for name, info in (response_actions_raw.get("actions") or {}).items()
    )

    confidence_raw = policies.get("confidence_thresholds") or {}
    escalation_raw = policies.get("escalation_rules") or {}
    tone_raw = policies.get("tone") or {}
    forbidden_raw = policies.get("forbidden_reply") or {}

    return PolicySet(
        response_actions=actions,
        output_format=str(response_actions_raw.get("output_format", "")).strip(),
        confidence=ConfidencePolicy(
            auto_send_min_confidence=_coerce_float(
                confidence_raw.get("auto_send_min_confidence"), 0.85
            ),
            clarify_send_min_confidence=_coerce_float(
                confidence_raw.get("clarify_send_min_confidence"), 0.75
            ),
            approval_fallback_min_confidence=_coerce_float(
                confidence_raw.get("approval_fallback_min_confidence"), 0.40
            ),
            rules=_string_tuple(confidence_raw.get("rules")),
        ),
        escalation=EscalationPolicy(
            escalate_when=_string_tuple(escalation_raw.get("escalate_when")),
            stop_trigger_keywords={
                str(name): _string_tuple(values)
                for name, values in (escalation_raw.get("stop_trigger_keywords") or {}).items()
            },
        ),
        tone=TonePolicy(
            language=str(tone_raw.get("language", "Russian")),
            rules=_string_tuple(tone_raw.get("rules")),
        ),
        forbidden_reply=ForbiddenReplyPolicy(
            patterns={
                str(name): _string_tuple(values)
                for name, values in (forbidden_raw.get("patterns") or {}).items()
            },
        ),
    )


def render_policy_block(policies: dict | None = None, path: str = "config/policies") -> str:
    """Render loaded policies into a text block suitable for prompt injection."""
    policy_set = load_policy_set(path) if policies is None else _coerce_policy_set(policies)

    lines: list[str] = []

    # Response actions & output format
    if policy_set.response_actions:
        lines.append("Rules:")
        for action in policy_set.response_actions:
            lines.append(f'- "{action.name}" \u2014 {action.description}')
    if policy_set.output_format:
        lines.append(policy_set.output_format)

    # Tone / communication rules
    for rule in policy_set.tone.rules:
        lines.append(f"- {rule}")

    # Confidence rules
    for rule in policy_set.confidence.rules:
        lines.append(f"- {rule}")

    # Explicit forbidden reply patterns
    if policy_set.forbidden_reply.patterns:
        lines.append("Forbidden reply patterns:")
        for reason, patterns in policy_set.forbidden_reply.patterns.items():
            lines.append(f'- "{reason}" -> {", ".join(patterns)}')

    return "\n".join(lines)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_policy_set(policies: dict | PolicySet) -> PolicySet:
    if isinstance(policies, PolicySet):
        return policies
    response_actions_raw = policies.get("response_actions") or {}
    actions = tuple(
        ResponseActionPolicy(
            name=name,
            description=str((info or {}).get("description", "")).strip(),
        )
        for name, info in (response_actions_raw.get("actions") or {}).items()
    )
    confidence_raw = policies.get("confidence_thresholds") or {}
    escalation_raw = policies.get("escalation_rules") or {}
    tone_raw = policies.get("tone") or {}
    forbidden_raw = policies.get("forbidden_reply") or {}
    return PolicySet(
        response_actions=actions,
        output_format=str(response_actions_raw.get("output_format", "")).strip(),
        confidence=ConfidencePolicy(
            auto_send_min_confidence=_coerce_float(
                confidence_raw.get("auto_send_min_confidence"), 0.85
            ),
            clarify_send_min_confidence=_coerce_float(
                confidence_raw.get("clarify_send_min_confidence"), 0.75
            ),
            approval_fallback_min_confidence=_coerce_float(
                confidence_raw.get("approval_fallback_min_confidence"), 0.40
            ),
            rules=_string_tuple(confidence_raw.get("rules")),
        ),
        escalation=EscalationPolicy(
            escalate_when=_string_tuple(escalation_raw.get("escalate_when")),
            stop_trigger_keywords={
                str(name): _string_tuple(values)
                for name, values in (escalation_raw.get("stop_trigger_keywords") or {}).items()
            },
        ),
        tone=TonePolicy(
            language=str(tone_raw.get("language", "Russian")),
            rules=_string_tuple(tone_raw.get("rules")),
        ),
        forbidden_reply=ForbiddenReplyPolicy(
            patterns={
                str(name): _string_tuple(values)
                for name, values in (forbidden_raw.get("patterns") or {}).items()
            },
        ),
    )
