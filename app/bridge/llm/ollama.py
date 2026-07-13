"""Native Ollama provider (local models via /api/chat)."""

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

# Local models can be slow on first load (model gets paged into RAM/VRAM).
_LOCAL_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class OllamaProvider(LLMProvider):
    async def complete(self, messages: list[dict]) -> str:
        converted = [_convert_message(m) for m in messages if m.get("role")]

        payload: dict = {
            "model": self.spec.model,
            "messages": converted,
            "stream": False,
        }
        options: dict = {"num_predict": self._max_tokens}
        if self._temperature is not None:
            options["temperature"] = self._temperature
        payload["options"] = options

        url = f"{self.spec.base_url.rstrip('/')}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"{self.spec.label}: transport error: {exc}", retryable=True) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.spec.label}: HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=retryable_status(resp.status_code),
            )

        try:
            data = resp.json()
            content = (data.get("message") or {}).get("content")
        except ValueError as exc:
            raise ProviderError(f"{self.spec.label}: malformed response payload") from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{self.spec.label}: empty completion")
        return content


def _convert_message(message: dict) -> dict:
    content = message.get("content")
    if isinstance(content, str):
        return {"role": message["role"], "content": content}

    texts: list[str] = []
    images: list[str] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url") or ""
                if url.startswith("data:"):
                    _, data = split_data_url(url)
                    images.append(data)

    converted: dict = {"role": message["role"], "content": "\n".join(texts)}
    if images:
        converted["images"] = images
    return converted
