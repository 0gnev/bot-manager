"""
High-level LLM service used by the bridge.

Same responsibilities the OpenClaw client used to have — prompt assembly,
structured-response coercion, audit logging — but calls providers directly
and fails over across the configured chain (e.g. cloud primary, local
fallback).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path

from bridge.audit import audit_log
from bridge.config import Settings
from bridge.llm.base import LLMProvider, ProviderError
from bridge.llm.context import sanitize_context
from bridge.llm.parsing import (
    coerce_approval_revision_response,
    coerce_knowledge_candidate_response,
    coerce_structured_response,
)
from bridge.llm.registry import build_chain
from bridge.policies.loader import render_policy_block
from bridge.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, settings: Settings, *, chain: list[LLMProvider] | None = None) -> None:
        self._settings = settings
        self._chain = chain if chain is not None else build_chain(settings)
        self._request_attempts = max(int(settings.llm_request_attempts or 1), 1)
        self._request_backoff_seconds = max(float(settings.llm_request_backoff_seconds or 0.0), 0.0)

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
        raw = await self._request_text(messages, mode="tutor_assistant")
        if raw is None:
            return "Не удалось сейчас ответить по базе знаний. Попробуйте переформулировать запрос."
        text = raw.strip()
        return text or "Не удалось подготовить ответ."

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
            booking_context=json.dumps(sanitize_context(booking_context), ensure_ascii=False, indent=2),
            student_message=student_message or "—",
            current_draft=current_draft,
            tutor_instruction=tutor_instruction,
        )
        raw = await self._request_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": tutor_instruction},
            ],
            mode="approval_revision",
        )
        if raw is None:
            return None
        parsed = coerce_approval_revision_response(raw)
        if parsed is None:
            logger.warning("Model returned invalid approval revision payload")
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
            booking_context=json.dumps(sanitize_context(booking_context), ensure_ascii=False, indent=2),
            source_question=source_question or "—",
            final_answer=final_answer,
        )
        raw = await self._request_text(
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
        parsed = coerce_knowledge_candidate_response(raw)
        if parsed is None:
            logger.warning("Model returned invalid knowledge candidate payload")
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
        safe_context = sanitize_context(booking_context)
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
        raw = await self._request_text(messages, mode="chat")
        if raw is None:
            return _fallback()
        result = coerce_structured_response(raw)
        if result is None or not isinstance(result, dict):
            logger.warning("Model returned invalid structured payload, falling back to escalation")
            return _fallback()
        return result

    async def _request_text(self, messages: list[dict], *, mode: str) -> str | None:
        """Try every provider in the chain; per-provider retries for transient errors."""
        for provider in self._chain:
            label = provider.spec.label
            for attempt in range(1, self._request_attempts + 1):
                await audit_log(
                    "ai",
                    "call_made",
                    actor="system",
                    detail={"provider": label, "mode": mode, "attempt": attempt},
                )
                started_at = time.perf_counter()
                try:
                    text = await provider.complete(messages)
                except ProviderError as exc:
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    await audit_log(
                        "ai",
                        "response_received",
                        actor="system",
                        outcome="failure",
                        detail={
                            "provider": label,
                            "mode": mode,
                            "error": str(exc)[:200],
                            "attempts": attempt,
                            "duration_ms": duration_ms,
                        },
                    )
                    if exc.retryable and attempt < self._request_attempts:
                        logger.warning("Retrying %s %s: %s", label, mode, exc)
                        await _sleep_before_retry(self._request_backoff_seconds, attempt)
                        continue
                    logger.error("Provider %s failed for %s: %s", label, mode, exc)
                    break  # move on to the next provider in the chain
                except Exception as exc:  # defensive: providers should raise ProviderError
                    logger.exception("Unexpected LLM failure on %s for %s", label, mode)
                    await audit_log(
                        "ai",
                        "response_received",
                        actor="system",
                        outcome="failure",
                        detail={
                            "provider": label,
                            "mode": mode,
                            "error": str(exc)[:200],
                            "attempts": attempt,
                            "duration_ms": int((time.perf_counter() - started_at) * 1000),
                        },
                    )
                    break
                else:
                    await audit_log(
                        "ai",
                        "response_received",
                        actor="system",
                        detail={
                            "provider": label,
                            "mode": mode,
                            "attempts": attempt,
                            "duration_ms": int((time.perf_counter() - started_at) * 1000),
                        },
                    )
                    return text
        logger.error("All LLM providers failed for %s", mode)
        return None


async def _sleep_before_retry(base_delay: float, attempt: int) -> None:
    if base_delay <= 0:
        return
    await asyncio.sleep(base_delay * (2 ** (attempt - 1)))


def _fallback() -> dict:
    return {
        "action": "escalate",
        "content": "Не могу ответить прямо сейчас — передаю преподавателю.",
        "confidence": 0.0,
    }
