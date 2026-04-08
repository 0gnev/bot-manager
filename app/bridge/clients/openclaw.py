"""
HTTP client for the OpenClaw AI gateway.

Uses the OpenResponses-compatible /v1/responses endpoint.
The system prompt instructs the model to return structured JSON:
  {"action": "answer"|"clarify"|"escalate", "content": "...", "confidence": 0.0-1.0}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from bridge.audit import audit_log
from bridge.config import Settings
from bridge.policies.loader import render_policy_block
from bridge.prompts.loader import render_prompt
from bridge.timezones import format_datetime

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0)


class OpenclawClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.openclaw_base_url.rstrip("/")
        self._model = getattr(settings, "openclaw_gateway_model", "openclaw")
        self._request_attempts = max(int(getattr(settings, "openclaw_request_attempts", 1) or 1), 1)
        self._request_backoff_seconds = max(
            float(getattr(settings, "openclaw_request_backoff_seconds", 0.0) or 0.0),
            0.0,
        )
        self._headers = {
            "Authorization": f"Bearer {settings.gateway_auth_token}",
            "Content-Type": "application/json",
        }

    # -- Public interface ------------------------------------------------------

    async def chat(
        self,
        message: str,
        booking_context: dict | None,
        history: list[dict],
        knowledge: list[dict] | None = None,
    ) -> dict:
        messages = self._build_messages(booking_context, history, knowledge)
        messages.append({"role": "user", "content": message})
        return await self._complete(messages)

    async def image(
        self,
        image_path: str,
        caption: str,
        booking_context: dict | None,
        history: list[dict],
        knowledge: list[dict] | None = None,
    ) -> dict:
        messages = self._build_messages(booking_context, history, knowledge)

        content: list[dict] = []
        if caption:
            content.append({"type": "text", "text": caption})
        else:
            content.append({"type": "text", "text": "Student sent an image."})

        try:
            img_bytes = Path(image_path).read_bytes()
            b64 = base64.b64encode(img_bytes).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        except Exception as exc:
            logger.error("Failed to read image %s: %s", image_path, exc)
            content.append({"type": "text", "text": "[image could not be read]"})

        messages.append({"role": "user", "content": content})
        return await self._complete(messages)

    async def tutor_assistant(
        self,
        message: str,
        knowledge: list[dict] | None = None,
    ) -> str:
        knowledge_section = ""
        if knowledge:
            chunks = []
            for doc in knowledge:
                chunks.append(f"### {doc['title']}\n{doc['content']}")
            knowledge_section = (
                "\nРелевантные материалы из базы знаний:\n"
                + "\n---\n".join(chunks)
                + "\n"
            )

        system = render_prompt(
            "tutor_assistant",
            knowledge_section=knowledge_section,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ]
        return await self._complete_text(messages)

    async def revise_approval_draft(
        self,
        *,
        student_message: str,
        current_draft: str,
        tutor_instruction: str,
        booking_context: dict | None = None,
    ) -> dict | None:
        system = render_prompt(
            "approval_revision",
            booking_context=json.dumps(_sanitize_context(booking_context), ensure_ascii=False, indent=2),
            student_message=student_message or "—",
            current_draft=current_draft,
            tutor_instruction=tutor_instruction,
        )
        raw = await self._request_response_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": tutor_instruction},
            ],
            mode="approval_revision",
        )
        if raw is None:
            return None
        parsed = _coerce_approval_revision_response(raw)
        if parsed is None:
            logger.warning("Openclaw returned invalid approval revision payload")
            return None
        return parsed

    async def propose_knowledge_candidate(
        self,
        *,
        source_question: str | None,
        final_answer: str,
        booking_context: dict | None = None,
    ) -> dict | None:
        system = render_prompt(
            "knowledge_candidate",
            booking_context=json.dumps(_sanitize_context(booking_context), ensure_ascii=False, indent=2),
            source_question=source_question or "—",
            final_answer=final_answer,
        )
        raw = await self._request_response_text(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Вопрос:\n{source_question or '—'}\n\nОтвет:\n{final_answer}",
                },
            ],
            mode="knowledge_candidate",
        )
        if raw is None:
            return None
        parsed = _coerce_knowledge_candidate_response(raw)
        if parsed is None:
            logger.warning("Openclaw returned invalid knowledge candidate payload")
            return None
        return parsed

    # -- Internals -------------------------------------------------------------

    def _build_messages(
        self, booking_context: dict | None, history: list[dict], knowledge: list[dict] | None = None
    ) -> list[dict]:
        knowledge_section = ""
        if knowledge:
            chunks = []
            for doc in knowledge:
                chunks.append(f"### {doc['title']}\n{doc['content']}")
            knowledge_section = (
                "\nRelevant knowledge base articles:\n"
                + "\n---\n".join(chunks)
                + "\n"
            )
        safe_context = _sanitize_context(booking_context)
        policy_block = render_policy_block()
        system = render_prompt(
            "system",
            booking_context=json.dumps(safe_context, ensure_ascii=False, indent=2),
            knowledge_section=knowledge_section,
            policy_block=policy_block,
        )
        messages: list[dict] = [{"role": "system", "content": system}]
        for entry in history:
            if entry.get("role") in ("user", "assistant"):
                if (
                    entry.get("direction") == "outbound"
                    and entry.get("delivery_status") not in {None, "sent", "delivered"}
                ):
                    continue
                messages.append({"role": entry["role"], "content": entry["content"]})
        return messages

    async def _complete(self, messages: list[dict]) -> dict:
        raw = await self._request_response_text(messages, mode="chat")
        if raw is None:
            return _fallback()
        result = _coerce_structured_response(raw)
        if result is None or not isinstance(result, dict):
            logger.warning("Openclaw returned invalid structured payload, falling back to escalation")
            return _fallback()
        return result

    async def _complete_text(self, messages: list[dict]) -> str:
        raw = await self._request_response_text(messages, mode="tutor_assistant")
        if raw is None:
            return "Не удалось сейчас ответить по базе знаний. Попробуйте переформулировать запрос."
        text = raw.strip() if isinstance(raw, str) else str(raw)
        return text or "Не удалось подготовить ответ."

    async def _request_response_text(self, messages: list[dict], *, mode: str) -> str | None:
        payload = {
            "model": self._model,
            "input": _messages_to_responses_input(messages),
            "reasoning": {"effort": "low"},
        }
        url = f"{self._base_url}/v1/responses"
        attempts = max(int(getattr(self, "_request_attempts", 1)), 1)
        for attempt in range(1, attempts + 1):
            await audit_log(
                "ai",
                "call_made",
                actor="system",
                detail={"url": url, "mode": mode, "attempt": attempt},
            )
            started_at = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(url, json=payload, headers=self._headers)
                    resp.raise_for_status()
                    raw = _extract_response_text(resp.json())
                    if raw is None:
                        logger.warning("Openclaw returned no text payload for %s", mode)
                        await audit_log(
                            "ai",
                            "response_received",
                            actor="system",
                            outcome="failure",
                            detail={
                                "mode": mode,
                                "error": "missing_text_payload",
                                "attempts": attempt,
                                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                            },
                        )
                        return None
                    await audit_log(
                        "ai",
                        "response_received",
                        actor="system",
                        detail={
                            "mode": mode,
                            "attempts": attempt,
                            "duration_ms": int((time.perf_counter() - started_at) * 1000),
                        },
                    )
                    return raw
            except httpx.HTTPStatusError as exc:
                if attempt < attempts and _should_retry_status(exc.response.status_code):
                    await _sleep_before_retry(self._request_backoff_seconds, attempt)
                    continue
                logger.error("Openclaw %s HTTP error: %s", mode, exc.response.text)
                await audit_log(
                    "ai",
                    "response_received",
                    actor="system",
                    outcome="failure",
                    detail={
                        "mode": mode,
                        "error": "http_error",
                        "attempts": attempt,
                        "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    },
                )
                return None
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < attempts:
                    logger.warning("Retrying Openclaw %s after transport error: %s", mode, exc)
                    await _sleep_before_retry(self._request_backoff_seconds, attempt)
                    continue
                logger.error("Openclaw %s failed: %s", mode, exc)
                await audit_log(
                    "ai",
                    "response_received",
                    actor="system",
                    outcome="failure",
                    detail={
                        "mode": mode,
                        "error": str(exc)[:200],
                        "attempts": attempt,
                        "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    },
                )
                return None
            except Exception as exc:
                logger.error("Openclaw %s failed: %s", mode, exc)
                await audit_log(
                    "ai",
                    "response_received",
                    actor="system",
                    outcome="failure",
                    detail={
                        "mode": mode,
                        "error": str(exc)[:200],
                        "attempts": attempt,
                        "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    },
                )
                return None
        return None


async def _sleep_before_retry(base_delay: float, attempt: int) -> None:
    if base_delay <= 0:
        return
    await asyncio.sleep(base_delay * (2 ** (attempt - 1)))


def _should_retry_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _messages_to_responses_input(messages: list[dict]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"system", "developer", "user", "assistant"}:
            continue
        content = _message_content_to_responses_content(message.get("content"))
        items.append(
            {
                "type": "message",
                "role": role,
                "content": content,
            }
        )
    return items


def _message_content_to_responses_content(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
            continue
        if part_type != "image_url":
            continue

        image_url = part.get("image_url") or {}
        url = image_url.get("url")
        if not isinstance(url, str) or not url:
            continue
        if url.startswith("data:"):
            header, _, data = url.partition(",")
            media_type = "image/jpeg"
            if header.startswith("data:"):
                media_type = header[5:].split(";", 1)[0] or media_type
            parts.append(
                {
                    "type": "input_image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )
            continue
        parts.append({"type": "input_image", "source": {"type": "url", "url": url}})

    return parts or ""


def _extract_response_text(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) else None

    output = payload.get("output")
    if not isinstance(output, list):
        return None

    text_chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            text_chunks.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in {"output_text", "input_text"}:
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                text_chunks.append(text)

    if not text_chunks:
        return None
    return "\n".join(text_chunks)


def _coerce_structured_response(raw: str) -> dict[str, Any] | None:
    parsed = _parse_json_object(raw)
    if parsed is not None:
        return parsed

    stripped = raw.strip()
    if not stripped:
        return None

    if _looks_like_escalation_text(stripped):
        return {"action": "escalate", "content": stripped, "confidence": 0.0}

    return {"action": "answer", "content": stripped, "confidence": 0.95}


def _coerce_approval_revision_response(raw: str) -> dict[str, Any] | None:
    parsed = _parse_json_object(raw)
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


def _coerce_knowledge_candidate_response(raw: str) -> dict[str, Any] | None:
    parsed = _parse_json_object(raw)
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


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    candidates = [raw.strip()]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL))

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


def _looks_like_escalation_text(text: str) -> bool:
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


def _sanitize_booking(booking: dict | None) -> dict | None:
    """Strip internal/sensitive fields before injecting into AI prompt."""
    if not booking:
        return None
    attendee = booking.get("attendee") or {}
    organizer = booking.get("organizer") or {}
    student_time_zone = attendee.get("timeZone")
    tutor_time_zone = organizer.get("timeZone")
    return {
        "title": booking.get("title", ""),
        "start_time": booking.get("start_time"),
        "end_time": booking.get("end_time"),
        "start_time_local": format_datetime(
            booking.get("start_time"),
            time_zone_name=student_time_zone,
            include_time_zone=bool(student_time_zone),
        ),
        "end_time_local": format_datetime(
            booking.get("end_time"),
            time_zone_name=student_time_zone,
            include_time_zone=bool(student_time_zone),
        ),
        "student_name": attendee.get("name", ""),
        "student_time_zone": student_time_zone,
        "tutor_name": organizer.get("name", ""),
        "tutor_time_zone": tutor_time_zone,
        "meeting_url": booking.get("meeting_url"),
        "status": booking.get("status"),
    }


def _sanitize_context(context: dict | None) -> dict:
    if not context:
        return {}
    if "contact" not in context and "booking" not in context and "bookings" not in context:
        return {"booking": _sanitize_booking(context)}

    safe_contact = dict(context.get("contact") or {})
    safe_contact.pop("telegram_user_id", None)
    safe_contact.pop("created_at", None)
    safe_contact.pop("updated_at", None)
    safe_bookings = []
    for booking in context.get("bookings") or []:
        sanitized = _sanitize_booking(booking)
        if sanitized is not None:
            safe_bookings.append(sanitized)
    return {
        "contact": safe_contact,
        "booking": _sanitize_booking(context.get("booking")),
        "bookings": safe_bookings,
        "context_source": context.get("context_source"),
    }


def _fallback() -> dict:
    return {
        "action": "escalate",
        "content": "Не могу ответить прямо сейчас — передаю преподавателю.",
        "confidence": 0.0,
    }
