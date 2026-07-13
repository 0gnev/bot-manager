from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bridge.llm import LLMService
from bridge.llm.base import LLMProvider, ProviderError, ProviderSpec
from bridge.llm.parsing import (
    coerce_knowledge_candidate_response,
    coerce_structured_response,
)
from bridge.llm.registry import build_chain, resolve_spec


def _settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        llm_provider="openai",
        llm_model="",
        llm_base_url="",
        llm_api_key="",
        openai_api_key="test-key",
        anthropic_api_key="",
        openrouter_api_key="",
        ollama_base_url="http://localhost:11434",
        lmstudio_base_url="http://localhost:1234/v1",
        llm_fallbacks="",
        llm_max_tokens=1024,
        llm_temperature=None,
        llm_request_attempts=2,
        llm_request_backoff_seconds=0.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _StubProvider(LLMProvider):
    def __init__(self, spec: ProviderSpec, *, reply: str | None = None, error: ProviderError | None = None):
        super().__init__(spec, max_tokens=1024, temperature=None)
        self._reply = reply
        self._error = error
        self.calls = 0

    async def complete(self, messages: list[dict]) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._reply is not None
        return self._reply


def _stub(kind: str, *, reply: str | None = None, error: ProviderError | None = None) -> _StubProvider:
    spec = ProviderSpec(kind=kind, model="m", base_url="http://x")
    return _StubProvider(spec, reply=reply, error=error)


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_plain_text_coerces_to_answer() -> None:
    result = coerce_structured_response("plain text instead of json")
    assert result == {"action": "answer", "content": "plain text instead of json", "confidence": 0.95}


def test_fenced_json_is_parsed() -> None:
    raw = '```json\n{"action":"answer","content":"Занятие в 12:00","confidence":0.93}\n```'
    result = coerce_structured_response(raw)
    assert result == {"action": "answer", "content": "Занятие в 12:00", "confidence": 0.93}


def test_escalation_sounding_text_escalates() -> None:
    result = coerce_structured_response("Не могу ответить, передам преподавателю")
    assert result is not None
    assert result["action"] == "escalate"


def test_knowledge_candidate_requires_title_when_saving() -> None:
    assert coerce_knowledge_candidate_response('{"should_save": true, "reason": "x"}') is None
    parsed = coerce_knowledge_candidate_response(
        '{"should_save": true, "reason": "x", "title": "T", "content_markdown": "Body"}'
    )
    assert parsed is not None and parsed["title"] == "T"


# ── Registry ─────────────────────────────────────────────────────────────────


def test_default_chain_is_single_openai_provider() -> None:
    chain = build_chain(_settings())
    assert len(chain) == 1
    assert chain[0].spec.kind == "openai"
    assert chain[0].spec.api_key == "test-key"
    assert chain[0].spec.base_url == "https://api.openai.com/v1"


def test_fallbacks_parse_provider_and_model_with_colons() -> None:
    chain = build_chain(_settings(llm_fallbacks="ollama:llama3.1:8b"))
    assert len(chain) == 2
    assert chain[1].spec.kind == "ollama"
    assert chain[1].spec.model == "llama3.1:8b"


def test_cloud_provider_without_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_spec(_settings(openai_api_key="", llm_api_key=""), "openai", "gpt-5-mini")


def test_local_providers_need_no_key() -> None:
    spec = resolve_spec(_settings(), "ollama", "llama3.1:8b")
    assert spec.api_key == ""
    assert spec.is_local


def test_custom_provider_requires_base_url() -> None:
    with pytest.raises(ValueError):
        resolve_spec(_settings(), "custom", "some-model")
    spec = resolve_spec(_settings(llm_base_url="http://10.0.0.5:8000/v1"), "custom", "some-model")
    assert spec.base_url == "http://10.0.0.5:8000/v1"


# ── Failover ─────────────────────────────────────────────────────────────────


def test_chat_returns_structured_payload_from_primary(monkeypatch) -> None:
    _patch_audit(monkeypatch)
    primary = _stub("openai", reply='{"action":"answer","content":"ok","confidence":0.9}')
    service = LLMService(_settings(), chain=[primary])
    result = asyncio.run(service.chat("hi", None, [], []))
    assert result == {"action": "answer", "content": "ok", "confidence": 0.9}


def test_failover_to_local_provider(monkeypatch) -> None:
    _patch_audit(monkeypatch)
    primary = _stub("openai", error=ProviderError("boom", retryable=False))
    fallback = _stub("ollama", reply='{"action":"answer","content":"local ok","confidence":0.8}')
    service = LLMService(_settings(), chain=[primary, fallback])
    result = asyncio.run(service.chat("hi", None, [], []))
    assert result["content"] == "local ok"
    assert primary.calls == 1  # non-retryable → no retry, straight to fallback


def test_retryable_error_is_retried_then_fails_over(monkeypatch) -> None:
    _patch_audit(monkeypatch)
    primary = _stub("openai", error=ProviderError("timeout", retryable=True))
    fallback = _stub("ollama", reply="plain answer")
    service = LLMService(_settings(llm_request_attempts=2), chain=[primary, fallback])
    result = asyncio.run(service.chat("hi", None, [], []))
    assert primary.calls == 2
    assert result["action"] == "answer"


def test_all_providers_down_escalates(monkeypatch) -> None:
    _patch_audit(monkeypatch)
    service = LLMService(
        _settings(),
        chain=[
            _stub("openai", error=ProviderError("down")),
            _stub("ollama", error=ProviderError("down too")),
        ],
    )
    result = asyncio.run(service.chat("hi", None, [], []))
    assert result["action"] == "escalate"
    assert result["confidence"] == 0.0


def _patch_audit(monkeypatch) -> None:
    async def fake_audit_log(*args, **kwargs):
        return None

    import bridge.llm.service as service_mod

    monkeypatch.setattr(service_mod, "audit_log", fake_audit_log)
