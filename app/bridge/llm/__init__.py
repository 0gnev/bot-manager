"""
Direct multi-provider LLM layer.

Replaces the former OpenClaw gateway. Providers are called directly over HTTP:

- ``openai``      — OpenAI Chat Completions (api.openai.com)
- ``openrouter``  — OpenRouter (OpenAI-compatible, many hosted models)
- ``anthropic``   — Anthropic Messages API
- ``ollama``      — local Ollama server (native /api/chat)
- ``lmstudio``    — local LM Studio server (OpenAI-compatible)
- ``custom``      — any OpenAI-compatible endpoint via LLM_BASE_URL

The primary provider plus optional fallbacks form a failover chain, so a
local model can back up a cloud one (or the other way around).
"""

from bridge.llm.service import LLMService

__all__ = ["LLMService"]
