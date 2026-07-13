from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class ProviderError(Exception):
    """A provider call failed. ``retryable`` hints whether a retry may help."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderSpec:
    """Resolved configuration for a single provider in the failover chain."""

    kind: str  # openai | openrouter | anthropic | ollama | lmstudio | custom
    model: str
    base_url: str
    api_key: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.kind}/{self.model}"

    @property
    def is_local(self) -> bool:
        return self.kind in {"ollama", "lmstudio"}


class LLMProvider(ABC):
    """One backend in the failover chain.

    ``complete`` receives OpenAI-style chat messages (``role`` + ``content``
    where content is a string or a list of text / image_url parts) and returns
    the assistant text. It raises :class:`ProviderError` on any failure.
    """

    def __init__(self, spec: ProviderSpec, *, max_tokens: int, temperature: float | None) -> None:
        self.spec = spec
        self._max_tokens = max_tokens
        self._temperature = temperature

    @abstractmethod
    async def complete(self, messages: list[dict]) -> str: ...


def retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def split_data_url(url: str) -> tuple[str, str]:
    """Split a data: URL into (media_type, base64_data)."""
    header, _, data = url.partition(",")
    media_type = "image/jpeg"
    if header.startswith("data:"):
        media_type = header[5:].split(";", 1)[0] or media_type
    return media_type, data
