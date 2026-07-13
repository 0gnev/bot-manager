"""
Coercion of raw model text into the structured payloads the bridge expects.

Models are instructed to answer with JSON like
``{"action": "answer"|"clarify"|"escalate", "content": "...", "confidence": 0.0-1.0}``
but smaller/local models often reply with plain text or fenced JSON, so the
parsers here are deliberately forgiving.
"""

from __future__ import annotations

import json
import re
from typing import Any


def coerce_structured_response(raw: str) -> dict[str, Any] | None:
    parsed = parse_json_object(raw)
    if parsed is not None:
        return parsed

    stripped = raw.strip()
    if not stripped:
        return None

    if looks_like_escalation_text(stripped):
        return {"action": "escalate", "content": stripped, "confidence": 0.0}

    return {"action": "answer", "content": stripped, "confidence": 0.95}


def coerce_approval_revision_response(raw: str) -> dict[str, Any] | None:
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return None
    decision = parsed.get("decision")
    content = parsed.get("content")
    if decision not in {"revise", "send"}:
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    confidence = parsed.get("confidence")
    if isinstance(confidence, (int, float)):
        parsed["confidence"] = float(confidence)
    else:
        parsed["confidence"] = 0.0
    parsed["content"] = content.strip()
    return parsed


def coerce_knowledge_candidate_response(raw: str) -> dict[str, Any] | None:
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return None

    should_save = parsed.get("should_save")
    if not isinstance(should_save, bool):
        return None
    parsed["should_save"] = should_save

    reason = parsed.get("reason")
    parsed["reason"] = reason.strip() if isinstance(reason, str) else ""

    if not should_save:
        return parsed

    title = parsed.get("title")
    content_markdown = parsed.get("content_markdown")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(content_markdown, str) or not content_markdown.strip():
        return None

    parsed["title"] = title.strip()
    parsed["content_markdown"] = content_markdown.strip()
    return parsed


def parse_json_object(raw: str) -> dict[str, Any] | None:
    candidates = [raw.strip()]
    candidates.extend(
        match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    )

    decoder = json.JSONDecoder()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def looks_like_escalation_text(text: str) -> bool:
    lowered = text.lower()
    cues = (
        "уточн",
        "преподавател",
        "передам",
        "не могу ответить",
        "не могу подсказать",
        "не знаю",
        "свяжитесь",
        "human",
        "tutor",
        "teacher",
    )
    return any(cue in lowered for cue in cues)
