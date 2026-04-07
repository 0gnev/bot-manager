"""
HTTP client for the OpenClaw AI gateway.

Uses the OpenAI-compatible /v1/chat/completions endpoint.
The system prompt instructs the model to return structured JSON:
  {"action": "answer"|"clarify"|"escalate", "content": "...", "confidence": 0.0-1.0}
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx

from bridge.audit import audit_log
from bridge.config import Settings
from bridge.policies.loader import render_policy_block
from bridge.prompts.loader import render_prompt

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0)


class OpenclawClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.openclaw_base_url.rstrip("/")
        self._model = getattr(settings, "openclaw_gateway_model", "openclaw")
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
                messages.append({"role": entry["role"], "content": entry["content"]})
        return messages

    async def _complete(self, messages: list[dict]) -> dict:
        payload = {"model": self._model, "messages": messages}
        url = f"{self._base_url}/v1/chat/completions"
        await audit_log("ai", "call_made", actor="system", detail={"url": url})
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
                raw: str = resp.json()["choices"][0]["message"]["content"]
                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Openclaw returned non-JSON, falling back to escalation")
                    await audit_log(
                        "ai",
                        "response_received",
                        actor="system",
                        outcome="failure",
                        detail={"error": "invalid_json"},
                    )
                    return _fallback()
                if not isinstance(result, dict):
                    logger.warning("Openclaw returned non-object JSON, falling back to escalation")
                    await audit_log(
                        "ai",
                        "response_received",
                        actor="system",
                        outcome="failure",
                        detail={"error": "invalid_payload_type"},
                    )
                    return _fallback()
                await audit_log(
                    "ai", "response_received",
                    actor="system",
                    detail={"action": result.get("action"), "confidence": result.get("confidence")},
                )
                return result
        except httpx.HTTPStatusError as exc:
            logger.error("Openclaw HTTP error: %s", exc.response.text)
            await audit_log("ai", "response_received", actor="system", outcome="failure", detail={"error": "http_error"})
            return _fallback()
        except Exception as exc:
            logger.error("Openclaw unreachable: %s", exc)
            await audit_log("ai", "response_received", actor="system", outcome="failure", detail={"error": str(exc)[:200]})
            return _fallback()

    async def _complete_text(self, messages: list[dict]) -> str:
        payload = {"model": self._model, "messages": messages}
        url = f"{self._base_url}/v1/chat/completions"
        await audit_log("ai", "call_made", actor="system", detail={"url": url, "mode": "tutor_assistant"})
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                text = raw.strip() if isinstance(raw, str) else str(raw)
                await audit_log(
                    "ai",
                    "response_received",
                    actor="system",
                    detail={"mode": "tutor_assistant"},
                )
                return text or "Не удалось подготовить ответ."
        except Exception as exc:
            logger.error("Openclaw tutor assistant failed: %s", exc)
            await audit_log(
                "ai",
                "response_received",
                actor="system",
                outcome="failure",
                detail={"mode": "tutor_assistant", "error": str(exc)[:200]},
            )
            return "Не удалось сейчас ответить по базе знаний. Попробуйте переформулировать запрос."


def _sanitize_booking(booking: dict | None) -> dict | None:
    """Strip internal/sensitive fields before injecting into AI prompt."""
    if not booking:
        return None
    attendee = booking.get("attendee") or {}
    organizer = booking.get("organizer") or {}
    return {
        "title": booking.get("title", ""),
        "start_time": booking.get("start_time"),
        "end_time": booking.get("end_time"),
        "student_name": attendee.get("name", ""),
        "tutor_name": organizer.get("name", ""),
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
