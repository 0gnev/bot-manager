"""
Builds the provider failover chain from Settings.

The primary provider comes from LLM_PROVIDER / LLM_MODEL. Fallbacks are a
comma-separated list of ``provider:model`` entries in LLM_FALLBACKS, e.g.::

    LLM_FALLBACKS=ollama:llama3.1:8b, anthropic:claude-haiku-4-5

Only the first ``:`` separates provider from model, so Ollama tags keep
working (``ollama:llama3.1:8b`` → provider ``ollama``, model ``llama3.1:8b``).
"""

from __future__ import annotations

import logging

from bridge.config import Settings
from bridge.llm.anthropic import AnthropicProvider
from bridge.llm.base import LLMProvider, ProviderSpec
from bridge.llm.ollama import OllamaProvider
from bridge.llm.openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)

KNOWN_PROVIDERS = ("openai", "openrouter", "anthropic", "ollama", "lmstudio", "custom")

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com",
}

_DEFAULT_MODELS = {
    "openai": "gpt-5-mini",
    "openrouter": "openai/gpt-5-mini",
    "anthropic": "claude-haiku-4-5",
    "ollama": "llama3.1:8b",
    "lmstudio": "local-model",
}


def build_chain(settings: Settings) -> list[LLMProvider]:
    """Return the ordered provider chain (primary first, then fallbacks)."""
    specs = [resolve_spec(settings, settings.llm_provider, settings.llm_model)]
    for entry in _parse_fallbacks(settings.llm_fallbacks):
        kind, model = entry
        try:
            specs.append(resolve_spec(settings, kind, model))
        except ValueError as exc:
            logger.warning("Skipping invalid LLM fallback %r: %s", entry, exc)

    return [
        _instantiate(spec, max_tokens=settings.llm_max_tokens, temperature=settings.llm_temperature)
        for spec in specs
    ]


def resolve_spec(settings: Settings, kind: str, model: str) -> ProviderSpec:
    kind = (kind or "").strip().lower()
    if kind not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown provider {kind!r}, expected one of {KNOWN_PROVIDERS}")

    model = (model or "").strip() or _DEFAULT_MODELS.get(kind, "")
    if not model:
        raise ValueError(f"provider {kind!r} requires an explicit model")

    base_url = ""
    api_key = ""
    extra_headers: dict[str, str] = {}

    if kind == "openai":
        base_url = settings.llm_base_url or _DEFAULT_BASE_URLS["openai"]
        api_key = settings.openai_api_key or settings.llm_api_key
    elif kind == "openrouter":
        base_url = settings.llm_base_url or _DEFAULT_BASE_URLS["openrouter"]
        api_key = settings.openrouter_api_key or settings.llm_api_key
        extra_headers = {"X-Title": "bot-manager"}
    elif kind == "anthropic":
        base_url = settings.llm_base_url or _DEFAULT_BASE_URLS["anthropic"]
        api_key = settings.anthropic_api_key or settings.llm_api_key
    elif kind == "ollama":
        base_url = settings.ollama_base_url
    elif kind == "lmstudio":
        base_url = settings.lmstudio_base_url
    elif kind == "custom":
        base_url = settings.llm_base_url
        api_key = settings.llm_api_key
        if not base_url:
            raise ValueError("provider 'custom' requires LLM_BASE_URL")

    if kind in {"openai", "openrouter", "anthropic"} and not api_key:
        raise ValueError(f"provider {kind!r} requires an API key")

    return ProviderSpec(
        kind=kind,
        model=model,
        base_url=base_url,
        api_key=api_key,
        extra_headers=extra_headers,
    )


def _instantiate(spec: ProviderSpec, *, max_tokens: int, temperature: float | None) -> LLMProvider:
    if spec.kind == "anthropic":
        return AnthropicProvider(spec, max_tokens=max_tokens, temperature=temperature)
    if spec.kind == "ollama":
        return OllamaProvider(spec, max_tokens=max_tokens, temperature=temperature)
    return OpenAICompatProvider(spec, max_tokens=max_tokens, temperature=temperature)


def _parse_fallbacks(raw: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        kind, _, model = chunk.partition(":")
        entries.append((kind.strip(), model.strip()))
    return entries
