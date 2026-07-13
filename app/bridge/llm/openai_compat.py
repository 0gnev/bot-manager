"""
OpenAI-compatible Chat Completions provider.

Covers OpenAI itself, OpenRouter, LM Studio, vLLM, llama.cpp server, and any
other endpoint that speaks POST {base_url}/chat/completions.
"""

from __future__ import annotations

import logging

import httpx

from bridge.llm.base import DEFAULT_TIMEOUT, LLMProvider, ProviderError, retryable_status

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    async def complete(self, messages: list[dict]) -> str:
        payload: dict = {
            "model": self.spec.model,
            "messages": messages,
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        headers = {"Content-Type": "application/json", **self.spec.extra_headers}
        if self.spec.api_key:
            headers["Authorization"] = f"Bearer {self.spec.api_key}"

        url = f"{self.spec.base_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"{self.spec.label}: transport error: {exc}", retryable=True) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.spec.label}: HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=retryable_status(resp.status_code),
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError) as exc:
            raise ProviderError(f"{self.spec.label}: malformed response payload") from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{self.spec.label}: empty completion")
        return content
