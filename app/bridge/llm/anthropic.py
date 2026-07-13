"""Anthropic Messages API provider."""

from __future__ import annotations

import logging

import httpx

from bridge.llm.base import (
    DEFAULT_TIMEOUT,
    LLMProvider,
    ProviderError,
    retryable_status,
    split_data_url,
)

logger = logging.getLogger(__name__)

_API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    async def complete(self, messages: list[dict]) -> str:
        system_parts: list[str] = []
        converted: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                system_parts.append(content if isinstance(content, str) else str(content))
                continue
            if role not in {"user", "assistant"}:
                continue
            converted.append({"role": role, "content": _convert_content(content)})

        payload: dict = {
            "model": self.spec.model,
            "max_tokens": self._max_tokens,
            "messages": converted,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.spec.api_key,
            "anthropic-version": _API_VERSION,
            **self.spec.extra_headers,
        }
        url = f"{self.spec.base_url.rstrip('/')}/v1/messages"
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
            blocks = data.get("content") or []
            text = "\n".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except ValueError as exc:
            raise ProviderError(f"{self.spec.label}: malformed response payload") from exc

        if not text.strip():
            raise ProviderError(f"{self.spec.label}: empty completion")
        return text


def _convert_content(content) -> str | list[dict]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            if url.startswith("data:"):
                media_type, data = split_data_url(url)
                parts.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    }
                )
            elif url:
                parts.append({"type": "image", "source": {"type": "url", "url": url}})
    return parts or ""
