from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge.clients import openclaw


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "openclaw",
            "output": [
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "plain text instead of json"}],
                }
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }


class _FakeJsonFenceResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "openclaw",
            "output": [
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "```json\n{\"action\":\"answer\",\"content\":\"Занятие в 12:00\",\"confidence\":0.93}\n```",
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }


class _FakeEscalationTextResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "openclaw",
            "output": [
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Нужно уточнить у преподавателя детали занятия.",
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }


class _FakeAsyncClient:
    requests: list[dict] = []
    response_cls = _FakeResponse

    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, *args, **kwargs) -> _FakeResponse:
        self.__class__.requests.append(kwargs)
        return self.__class__.response_cls()


def test_openclaw_plain_text_falls_back_to_answer(monkeypatch) -> None:
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.response_cls = _FakeResponse
    settings = SimpleNamespace(
        openclaw_base_url="http://openclaw:18789",
        gateway_auth_token="token",
        openclaw_gateway_model="openclaw",
    )
    audit_events: list[tuple[str, str, dict]] = []

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        audit_events.append((event_type, action, kwargs))

    monkeypatch.setattr(openclaw.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(openclaw, "audit_log", fake_audit_log)

    client = openclaw.OpenclawClient(settings)
    result = asyncio.run(
        client.chat(
            message="Когда занятие?",
            booking_context={},
            history=[],
            knowledge=[],
        )
    )

    assert result["action"] == "answer"
    assert result["content"] == "plain text instead of json"
    assert result["confidence"] == 0.95
    assert _FakeAsyncClient.requests[0]["json"]["model"] == "openclaw"
    assert _FakeAsyncClient.requests[0]["json"]["input"][-1]["role"] == "user"
    assert _FakeAsyncClient.requests[0]["json"]["input"][-1]["content"] == "Когда занятие?"
    assert any(event_type == "ai" and action == "response_received" for event_type, action, _ in audit_events)


def test_openclaw_extracts_json_from_markdown_fence(monkeypatch) -> None:
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.response_cls = _FakeJsonFenceResponse
    settings = SimpleNamespace(
        openclaw_base_url="http://openclaw:18789",
        gateway_auth_token="token",
        openclaw_gateway_model="openclaw",
    )

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        return None

    monkeypatch.setattr(openclaw.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(openclaw, "audit_log", fake_audit_log)

    client = openclaw.OpenclawClient(settings)
    result = asyncio.run(client.chat(message="Когда занятие?", booking_context={}, history=[], knowledge=[]))

    assert result == {
        "action": "answer",
        "content": "Занятие в 12:00",
        "confidence": 0.93,
    }


def test_openclaw_escalates_when_plain_text_requests_human_help(monkeypatch) -> None:
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.response_cls = _FakeEscalationTextResponse
    settings = SimpleNamespace(
        openclaw_base_url="http://openclaw:18789",
        gateway_auth_token="token",
        openclaw_gateway_model="openclaw",
    )

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        return None

    monkeypatch.setattr(openclaw.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(openclaw, "audit_log", fake_audit_log)

    client = openclaw.OpenclawClient(settings)
    result = asyncio.run(client.chat(message="Когда занятие?", booking_context={}, history=[], knowledge=[]))

    assert result["action"] == "escalate"
    assert result["confidence"] == 0.0
