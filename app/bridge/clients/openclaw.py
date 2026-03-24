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

from bridge.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0)

_SYSTEM_PROMPT = """\
You are a friendly assistant for an online tutoring service that prepares students for the EGE exam in computer science.
You help students with questions about their upcoming session.

Session info:
{booking_context}
{knowledge_section}
Respond ONLY with valid JSON (no markdown fences):
{{
  "action": "answer" | "clarify" | "escalate",
  "content": "<your reply to the student>",
  "confidence": <float 0.0–1.0>
}}

Rules:
- "answer"   — you can reply confidently from the session info or knowledge base
- "clarify"  — you need more information from the student
- "escalate" — the question requires the tutor's personal judgment (scheduling changes, grades, individual feedback)
- Use the knowledge base articles when they are relevant to the student's question
- Write content in Russian
- NEVER reveal internal data: emails, phone numbers, IDs, system fields, JSON structures
- NEVER claim you can send emails, make calls, or access external services
- You can only communicate with the student through this chat
- Keep replies concise and helpful
- If unsure, escalate to the tutor rather than guessing
"""


class OpenclawClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.openclaw_base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.gateway_auth_token}",
            "Content-Type": "application/json",
        }

    # ── Public interface ───────────────────────────────────────────────────────

    async def chat(
        self,
        message: str,
        booking_context: dict,
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
        booking_context: dict,
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

    # ── Internals ──────────────────────────────────────────────────────────────

    def _build_messages(
        self, booking_context: dict, history: list[dict], knowledge: list[dict] | None = None
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
        safe_context = _sanitize_booking(booking_context)
        system = _SYSTEM_PROMPT.format(
            booking_context=json.dumps(safe_context, ensure_ascii=False, indent=2),
            knowledge_section=knowledge_section,
        )
        messages: list[dict] = [{"role": "system", "content": system}]
        for entry in history:
            if entry.get("role") in ("user", "assistant"):
                messages.append({"role": entry["role"], "content": entry["content"]})
        return messages

    async def _complete(self, messages: list[dict]) -> dict:
        payload = {"model": "default", "messages": messages}
        url = f"{self._base_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
                raw: str = resp.json()["choices"][0]["message"]["content"]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    # Model replied in plain text — wrap as a direct answer
                    logger.warning("Openclaw returned non-JSON, wrapping as answer")
                    return {"action": "answer", "content": raw, "confidence": 0.8}
        except httpx.HTTPStatusError as exc:
            logger.error("Openclaw HTTP error: %s", exc.response.text)
            return _fallback()
        except Exception as exc:
            logger.error("Openclaw unreachable: %s", exc)
            return _fallback()


def _sanitize_booking(booking: dict) -> dict:
    """Strip internal/sensitive fields before injecting into AI prompt."""
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


def _fallback() -> dict:
    return {
        "action": "escalate",
        "content": "Не могу ответить прямо сейчас — передаю преподавателю.",
        "confidence": 0.0,
    }
